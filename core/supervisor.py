"""
Supervisor — QA review agent.
Reviews completed work in 4_review, runs automated checks,
approves (→ 6_completed) or sends back for rework (→ 5_rework).
"""

import json
import logging
from pathlib import Path
from typing import Optional

from core.agent import BaseAgent
from core.state_machine import StateMachine
from core.task_card import TaskCard, ReviewNote, _now
from core.token_tracker import TokenTracker
from tools import file_tools, code_tools

logger = logging.getLogger(__name__)

SUPERVISOR_SYSTEM_PROMPT = """You are the Quality Supervisor of an AI Factory.
You review work completed by other agents.

For each piece of work, you:
1. Read the original task requirements and acceptance criteria
2. Examine all output files using the read_file tool
3. Run tests if applicable using run_tests or run_python
4. Check for:
   - Correctness: Does it meet acceptance criteria?
   - Quality: Is the code/output clean and well-structured?
   - Completeness: Are all requirements addressed?
   - Security: Any obvious vulnerabilities?

Score on 0-10 scale:
- 9-10: Excellent, exceeds requirements
- 7-8: Good, meets all requirements
- 5-6: Needs minor fixes
- 3-4: Significant issues
- 0-2: Fundamentally wrong

ALWAYS output your review as a JSON object (nothing else):
{
  "score": 8,
  "verdict": "APPROVE",
  "notes": ["Good code structure", "All criteria met"],
  "summary": "Task completed successfully with clean implementation"
}

If score < 7, verdict MUST be "REWORK" and notes MUST include specific issues to fix.
If score >= 7, verdict is "APPROVE".
"""


class Supervisor(BaseAgent):
    """
    Reviews tasks in 4_review.
    Approves good work → 6_completed.
    Sends back bad work → 5_rework (up to max_attempts).
    """

    def __init__(
        self,
        agent_id: str = "supervisor-001",
        name: str = "Quality Supervisor",
        model: str = "claude-sonnet-4-6",
        state_machine: Optional[StateMachine] = None,
        token_tracker: Optional[TokenTracker] = None,
        approval_threshold: float = 7.0,
        max_turns: int = 10,
    ):
        self.state_machine = state_machine
        self.approval_threshold = approval_threshold

        # Инструменты для ревью: чтение файлов + запуск кода/тестов
        self._tool_names = [
            "read_file", "list_directory", "file_exists",
            "run_python", "run_tests", "run_command",
        ]

        super().__init__(
            agent_id=agent_id,
            name=name,
            model=model,
            system_prompt=SUPERVISOR_SYSTEM_PROMPT,
            max_turns=max_turns,
            token_tracker=token_tracker,
        )

    def tools_definition(self) -> list[dict]:
        """Return tool definitions for supervisor (read-only + test tools)."""
        definitions = []
        for module in [file_tools, code_tools]:
            for tdef in module.TOOL_DEFINITIONS:
                if tdef["name"] in self._tool_names:
                    definitions.append(tdef)
        return definitions

    def execute_tool(self, name: str, inputs: dict) -> str:
        """Route tool to module."""
        if name in ("read_file", "list_directory", "file_exists", "create_directory"):
            return file_tools.execute(name, inputs)
        elif name in ("run_python", "run_tests", "run_command"):
            return code_tools.execute(name, inputs)
        return f"Unknown tool: {name}"

    def review_task(self, task: TaskCard) -> TaskCard:
        """
        Review a single task:
        1. Build review context (task + output files)
        2. Call Claude for qualitative review
        3. Parse verdict
        4. Route: APPROVE → 6_completed, REWORK → 5_rework
        """
        message = self._build_review_message(task)

        result = self.run(message, task_id=task.id)

        # Парсим вердикт
        verdict = self._parse_verdict(result.response)

        score = verdict.get("score", 0)
        verdict_str = verdict.get("verdict", "REWORK")
        notes = verdict.get("notes", [])
        summary = verdict.get("summary", "")

        # Создаём запись ревью
        review = ReviewNote(
            attempt=task.attempt,
            score=score,
            notes=notes,
            verdict=verdict_str,
        )
        task.review_notes.append(review)
        task.review_score = score
        task.cost += result.cost

        if not self.state_machine:
            logger.error("StateMachine не инициализирован")
            return task

        if score >= self.approval_threshold and verdict_str == "APPROVE":
            # Одобрено
            self.state_machine.transition(task, "6_completed")
            logger.info(
                f"[{task.id}] ОДОБРЕНО (score={score}) — {summary}"
            )
            # Разблокируем зависимые задачи
            self.state_machine.unblock_dependents(task)

        elif task.attempt >= task.max_attempts:
            # Превышен лимит попыток — эскалация
            task.tags.append("ESCALATED")
            review.verdict = "ESCALATED"
            review.notes.append("Превышен лимит попыток — требуется ручная проверка")
            self.state_machine.transition(task, "6_completed")
            logger.warning(
                f"[{task.id}] ЭСКАЛАЦИЯ — {task.attempt} попыток, score={score}"
            )
        else:
            # На доработку
            task.attempt += 1
            self.state_machine.transition(task, "5_rework")
            logger.info(
                f"[{task.id}] ДОРАБОТКА (score={score}, попытка {task.attempt}) — "
                f"{', '.join(notes[:3])}"
            )

        return task

    def review_next(self) -> TaskCard | None:
        """
        Pick and review the next task from 4_review.
        Returns reviewed task or None.
        """
        if not self.state_machine:
            return None

        tasks = self.state_machine.get_tasks("4_review")
        if not tasks:
            return None

        task = tasks[0]  # Берём по приоритету (уже отсортировано)
        return self.review_task(task)

    def _build_review_message(self, task: TaskCard) -> str:
        """Build review context for Claude."""
        parts = [
            f"# Review Task: {task.title}",
            f"\n## Original Requirements:\n{task.description}",
        ]

        if task.acceptance_criteria:
            parts.append("\n## Acceptance Criteria:")
            for i, ac in enumerate(task.acceptance_criteria, 1):
                parts.append(f"{i}. {ac}")

        if task.workspace_path:
            parts.append(f"\n## Workspace: {task.workspace_path}")
            parts.append(
                "List the workspace directory and read the output files to review them."
            )

        if task.output_files:
            parts.append(f"\n## Output files: {', '.join(task.output_files[:20])}")

        if task.review_notes:
            parts.append(f"\n## Previous reviews (attempt {task.attempt}):")
            for rn in task.review_notes:
                parts.append(f"- Score: {rn.score}, Verdict: {rn.verdict}")
                for n in rn.notes:
                    parts.append(f"  - {n}")

        return "\n".join(parts)

    def _parse_verdict(self, response: str) -> dict:
        """Parse JSON verdict from supervisor response."""
        text = response.strip()

        # Ищем JSON
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()

        try:
            json_start = text.index("{")
            json_end = text.rindex("}") + 1
            return json.loads(text[json_start:json_end])
        except (ValueError, json.JSONDecodeError) as e:
            logger.error(f"Ошибка парсинга вердикта: {e}")
            return {
                "score": 0,
                "verdict": "REWORK",
                "notes": ["Не удалось получить вердикт от ревьюера"],
                "summary": "Review parsing error",
            }
