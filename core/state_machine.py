"""
StateMachine — manages task card transitions between pipeline folders.
Uses file-based locking via portalocker to prevent double-pickup.
"""

import logging
import shutil
from pathlib import Path

import portalocker

from core.task_card import TaskCard, _now

logger = logging.getLogger(__name__)

# Допустимые переходы между стадиями
TRANSITIONS: dict[str, list[str]] = {
    "0_inbox":       ["1_planning"],
    "1_planning":    ["2_ready"],
    "2_ready":       ["3_in_progress"],
    "3_in_progress": ["4_review", "5_rework"],
    "4_review":      ["5_rework", "6_completed"],
    "5_rework":      ["3_in_progress"],
    "6_completed":   ["7_archived"],
    "7_archived":    [],
}

# Маппинг папки → статус
STAGE_STATUS: dict[str, str] = {
    "0_inbox":       "new",
    "1_planning":    "planning",
    "2_ready":       "ready",
    "3_in_progress": "in_progress",
    "4_review":      "review",
    "5_rework":      "rework",
    "6_completed":   "completed",
    "7_archived":    "archived",
}


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class LockError(Exception):
    """Raised when a task card cannot be locked."""
    pass


class StateMachine:
    """
    Manages task card lifecycle through pipeline folders.

    Operations:
    - transition(): Move task card between stages
    - lock_task() / unlock_task(): Prevent double-pickup
    - get_tasks(): List tasks in a stage
    - get_ready_tasks(): Find tasks matching skills
    """

    def __init__(self, pipeline_root: str | Path):
        self.pipeline_root = Path(pipeline_root)

    def _stage_path(self, stage: str) -> Path:
        return self.pipeline_root / stage

    def _task_path(self, stage: str, task_id: str) -> Path:
        return self._stage_path(stage) / f"{task_id}.json"

    def _lock_path(self, task_id: str) -> Path:
        return self.pipeline_root / f"{task_id}.lock"

    def validate_transition(self, from_stage: str, to_stage: str) -> bool:
        """Check if transition is allowed."""
        allowed = TRANSITIONS.get(from_stage, [])
        return to_stage in allowed

    def lock_task(self, task_id: str) -> portalocker.Lock:
        """
        Acquire exclusive lock on a task card.
        Returns lock object (caller must unlock when done).
        Raises LockError if already locked.
        """
        lock_path = self._lock_path(task_id)
        try:
            lock = portalocker.Lock(
                str(lock_path),
                mode="w",
                timeout=0,
                flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
            )
            lock.acquire()
            return lock
        except (portalocker.LockException, portalocker.AlreadyLocked):
            raise LockError(f"Задача {task_id} уже заблокирована другим агентом")

    def unlock_task(self, task_id: str, lock: portalocker.Lock):
        """Release lock and remove lock file."""
        try:
            lock.release()
        except Exception:
            pass
        lock_path = self._lock_path(task_id)
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)

    def transition(self, task: TaskCard, to_stage: str) -> TaskCard:
        """
        Move task card from current stage to a new stage.
        1. Validate transition
        2. Acquire lock
        3. Update task fields
        4. Move file
        5. Release lock
        """
        from_stage = task.stage_folder

        if not self.validate_transition(from_stage, to_stage):
            raise TransitionError(
                f"Переход {from_stage} → {to_stage} недопустим. "
                f"Допустимые: {TRANSITIONS.get(from_stage, [])}"
            )

        lock = self.lock_task(task.id)
        try:
            # Обновляем поля задачи
            task.stage_folder = to_stage
            task.status = STAGE_STATUS.get(to_stage, to_stage)

            # Временные метки
            if to_stage == "3_in_progress" and not task.started_at:
                task.started_at = _now()
            elif to_stage == "6_completed":
                task.completed_at = _now()

            # Удаляем старый файл
            old_path = self._task_path(from_stage, task.id)
            if old_path.exists():
                old_path.unlink()

            # Сохраняем в новую папку
            new_folder = self._stage_path(to_stage)
            new_folder.mkdir(parents=True, exist_ok=True)
            task.save(new_folder)

            logger.info(f"[{task.id}] {from_stage} → {to_stage}")

            # Обновляем BOARD.md
            self._refresh_board()

            return task

        finally:
            self.unlock_task(task.id, lock)

    def _refresh_board(self):
        """Update BOARD.md after each transition."""
        try:
            from core.board import generate_board
            board_path = self.pipeline_root.parent / "BOARD.md"
            generate_board(self.pipeline_root, board_path)
        except Exception as e:
            logger.debug(f"Board refresh skipped: {e}")

    def get_tasks(self, stage: str) -> list[TaskCard]:
        """List all task cards in a pipeline stage."""
        return TaskCard.list_in_folder(self._stage_path(stage))

    def get_ready_tasks(self, skills: list[str]) -> list[TaskCard]:
        """
        Find tasks in 2_ready that match given skills.
        A task matches if all its required_skills are in the provided skills list.
        """
        ready = self.get_tasks("2_ready")
        matching = []
        for task in ready:
            if not task.required_skills:
                matching.append(task)
            elif all(s in skills for s in task.required_skills):
                matching.append(task)
        return matching

    def get_rework_tasks(self, skills: list[str]) -> list[TaskCard]:
        """Find tasks in 5_rework matching given skills."""
        rework = self.get_tasks("5_rework")
        matching = []
        for task in rework:
            if not task.required_skills:
                matching.append(task)
            elif all(s in skills for s in task.required_skills):
                matching.append(task)
        return matching

    def check_dependencies_met(self, task: TaskCard) -> bool:
        """Check if all dependencies of a task are completed."""
        if not task.depends_on:
            return True
        completed = self.get_tasks("6_completed")
        archived = self.get_tasks("7_archived")
        done_ids = {t.id for t in completed} | {t.id for t in archived}
        return all(dep_id in done_ids for dep_id in task.depends_on)

    def unblock_dependents(self, completed_task: TaskCard):
        """
        After a task is completed, check if it unblocks any tasks in 1_planning.
        Move unblocked tasks to 2_ready.
        """
        planning = self.get_tasks("1_planning")
        for task in planning:
            if completed_task.id in task.depends_on:
                if self.check_dependencies_met(task):
                    self.transition(task, "2_ready")
                    logger.info(
                        f"[{task.id}] Разблокирована после завершения {completed_task.id}"
                    )

    def get_pipeline_summary(self) -> dict[str, int]:
        """Get task count per stage — for dashboard."""
        summary = {}
        for stage in TRANSITIONS:
            folder = self._stage_path(stage)
            if folder.exists():
                count = len(list(folder.glob("task-*.json")))
            else:
                count = 0
            summary[stage] = count
        return summary
