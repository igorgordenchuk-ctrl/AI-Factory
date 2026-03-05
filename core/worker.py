"""
Worker — task execution agent.
Picks up tasks from 2_ready and 5_rework, executes them using tools,
produces artifacts in workspace/, then moves tasks to 4_review.
"""

import logging
from pathlib import Path
from typing import Optional

from core.agent import BaseAgent
from core.skill_registry import SkillRegistry
from core.state_machine import StateMachine, LockError
from core.task_card import TaskCard, ReviewNote
from core.token_tracker import TokenTracker
from tools import file_tools, code_tools

logger = logging.getLogger(__name__)

# Маппинг имён инструментов → модули
TOOL_MODULES = {
    "read_file": file_tools,
    "write_file": file_tools,
    "list_directory": file_tools,
    "create_directory": file_tools,
    "file_exists": file_tools,
    "run_python": code_tools,
    "run_tests": code_tools,
    "run_command": code_tools,
}


class Worker(BaseAgent):
    """
    Worker agent with specific skills.
    Monitors 2_ready and 5_rework for matching tasks.
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        skills: list[str],
        skill_registry: SkillRegistry,
        model: str = "claude-sonnet-4-6",
        state_machine: Optional[StateMachine] = None,
        token_tracker: Optional[TokenTracker] = None,
        max_turns: int = 10,
    ):
        self.skills = skills
        self.skill_registry = skill_registry
        self.state_machine = state_machine

        # Собираем system prompt из навыков
        system_prompt = self._build_system_prompt()

        # Собираем определения инструментов
        tool_names = skill_registry.tools_for_skills(skills)
        self._tool_names = tool_names

        super().__init__(
            agent_id=agent_id,
            name=name,
            model=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            token_tracker=token_tracker,
        )

    def _build_system_prompt(self) -> str:
        """Assemble system prompt from skill definitions."""
        base = (
            "You are a Worker agent in an AI Factory.\n"
            "You execute tasks by using the provided tools.\n"
            "Always write output files to the workspace directory specified in the task.\n"
            "Create directories as needed before writing files.\n"
            "After completing the task, briefly summarize what you did.\n\n"
        )
        skills_prompt = self.skill_registry.prompt_for_skills(self.skills)
        return base + skills_prompt

    def tools_definition(self) -> list[dict]:
        """Return tool definitions for tools available to this worker."""
        definitions = []
        for tool_name in self._tool_names:
            module = TOOL_MODULES.get(tool_name)
            if module:
                for tdef in module.TOOL_DEFINITIONS:
                    if tdef["name"] == tool_name:
                        definitions.append(tdef)
                        break
        return definitions

    def execute_tool(self, name: str, inputs: dict) -> str:
        """Route tool execution to the appropriate module."""
        # Применяем workspace prefix если нужен
        if self._current_workspace and "path" in inputs:
            path = inputs["path"]
            if not Path(path).is_absolute():
                inputs["path"] = str(Path(self._current_workspace) / path)

        module = TOOL_MODULES.get(name)
        if module:
            return module.execute(name, inputs)
        return f"Unknown tool: {name}"

    def execute_task(self, task: TaskCard) -> TaskCard:
        """
        Execute a single task.
        1. Set up workspace
        2. Build message from task description + review notes (if rework)
        3. Run tool-use loop
        4. Collect output files
        5. Update task card
        """
        self._current_workspace = task.workspace_path

        # Создаём рабочую папку
        Path(task.workspace_path).mkdir(parents=True, exist_ok=True)

        # Формируем сообщение
        message = self._build_task_message(task)

        # Запускаем агента
        result = self.run(message, task_id=task.id)

        # Обновляем карточку
        task.cost += result.cost
        task.tokens_used = {
            "input": task.tokens_used.get("input", 0) + result.tokens.get("input", 0),
            "output": task.tokens_used.get("output", 0) + result.tokens.get("output", 0),
        }

        # Собираем выходные файлы (из tool_log)
        for log_entry in result.tool_log:
            if log_entry["tool"] == "write_file" and "OK:" in log_entry.get("result", ""):
                # Извлекаем путь из лога
                path_part = log_entry.get("input", "")
                if path_part:
                    task.output_files.append(path_part[:200])

        if result.error:
            logger.error(f"[{self.agent_id}] Ошибка выполнения {task.id}: {result.error}")

        self._current_workspace = ""
        return task

    def _build_task_message(self, task: TaskCard) -> str:
        """Build the message to send to Claude for task execution."""
        parts = [
            f"# Task: {task.title}",
            f"\n{task.description}",
        ]

        if task.acceptance_criteria:
            parts.append("\n## Acceptance Criteria:")
            for i, ac in enumerate(task.acceptance_criteria, 1):
                parts.append(f"{i}. {ac}")

        parts.append(f"\n## Workspace: {task.workspace_path}")
        parts.append("Write all output files to this workspace directory.")

        # Если это доработка — добавляем замечания ревьюера
        if task.status == "rework" and task.review_notes:
            last_review = task.review_notes[-1]
            parts.append("\n## REWORK REQUIRED - Reviewer Feedback:")
            parts.append(f"Score: {last_review.score}/10")
            for note in last_review.notes:
                parts.append(f"- {note}")
            parts.append("\nFix the issues listed above and ensure all acceptance criteria are met.")

        # Список существующих файлов в workspace
        ws = Path(task.workspace_path)
        if ws.exists():
            existing = [str(f.relative_to(ws)) for f in ws.rglob("*") if f.is_file()]
            if existing:
                parts.append(f"\n## Existing files in workspace:\n{chr(10).join(existing)}")

        return "\n".join(parts)

    def pick_and_execute(self) -> TaskCard | None:
        """
        Pick one matching task from 2_ready or 5_rework and execute it.
        Returns the task card (moved to 4_review) or None if no tasks found.
        """
        if not self.state_machine:
            logger.error("StateMachine не инициализирован")
            return None

        # Сначала проверяем доработки (приоритет)
        tasks = self.state_machine.get_rework_tasks(self.skills)
        if not tasks:
            tasks = self.state_machine.get_ready_tasks(self.skills)
        if not tasks:
            return None

        # Пытаемся захватить задачу
        for task in tasks:
            try:
                # ready → in_progress
                from_stage = task.stage_folder
                if from_stage == "5_rework":
                    self.state_machine.transition(task, "3_in_progress")
                else:
                    self.state_machine.transition(task, "3_in_progress")

                task.assigned_agent_id = self.agent_id
                logger.info(f"[{self.agent_id}] Взял задачу: {task.id} ({task.title})")

                # Выполняем
                task = self.execute_task(task)

                # in_progress → review
                self.state_machine.transition(task, "4_review")
                logger.info(f"[{self.agent_id}] Задача {task.id} отправлена на проверку")

                return task

            except LockError:
                # Задача уже захвачена другим агентом
                continue
            except Exception as e:
                logger.error(f"[{self.agent_id}] Ошибка обработки {task.id}: {e}")
                continue

        return None

    _current_workspace: str = ""
