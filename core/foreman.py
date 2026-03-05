"""
Foreman — task decomposition agent.
Takes user requests from 0_inbox, breaks them into subtasks,
determines required skills, and creates task cards in the pipeline.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from core.agent import BaseAgent, AgentResult
from core.skill_registry import SkillRegistry
from core.state_machine import StateMachine
from core.task_card import TaskCard, _new_id, _now
from core.token_tracker import TokenTracker

logger = logging.getLogger(__name__)

FOREMAN_SYSTEM_PROMPT = """You are the Foreman of an AI Factory.
Your job is to decompose a user's request into concrete, actionable subtasks.

For each subtask, provide:
1. title: Short descriptive name
2. description: Detailed requirements and instructions for the worker
3. acceptance_criteria: List of checkable conditions that define "done"
4. required_skills: Which specialist(s) are needed (from the available skills list)
5. depends_on_index: List of subtask indices (0-based) that must complete before this one
6. priority: 1-10 (1=most urgent)
7. tags: Relevant tags

Think carefully about:
- Task ordering: what must be done first?
- Parallelism: which tasks can run simultaneously?
- Granularity: tasks should be small enough for one agent to complete, but not trivially small
- Testing: include test/validation subtasks where appropriate

Output your plan as a JSON object:
{
  "project_title": "Short project name",
  "subtasks": [
    {
      "title": "...",
      "description": "...",
      "acceptance_criteria": ["...", "..."],
      "required_skills": ["python_developer"],
      "depends_on_index": [],
      "priority": 3,
      "tags": ["backend"]
    }
  ]
}

Available skills: SKILLS_LIST
"""


class Foreman(BaseAgent):
    """
    Decomposes user tasks into subtasks with dependency graphs.
    Operates on tasks in 0_inbox → moves to 1_planning → 2_ready.
    """

    def __init__(
        self,
        agent_id: str = "foreman-001",
        name: str = "Factory Foreman",
        model: str = "claude-sonnet-4-6",
        skill_registry: Optional[SkillRegistry] = None,
        state_machine: Optional[StateMachine] = None,
        token_tracker: Optional[TokenTracker] = None,
        max_turns: int = 15,
    ):
        self.skill_registry = skill_registry
        self.state_machine = state_machine

        # Формируем system prompt со списком навыков
        skills_list = "none loaded"
        if skill_registry:
            skills_list = ", ".join(
                f"{s.skill_id} ({s.name})" for s in skill_registry.all_skills()
            )
        system_prompt = FOREMAN_SYSTEM_PROMPT.replace("SKILLS_LIST", skills_list)

        super().__init__(
            agent_id=agent_id,
            name=name,
            model=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            token_tracker=token_tracker,
        )

    def decompose(self, task: TaskCard) -> list[TaskCard]:
        """
        Decompose a parent task into subtasks.
        1. Call Claude to analyze and break down the task
        2. Parse the JSON response into TaskCards
        3. Set up dependencies between cards
        4. Return list of subtask cards
        """
        message = (
            f"Decompose this task into subtasks:\n\n"
            f"Title: {task.title}\n"
            f"Description: {task.description}\n"
        )
        if task.acceptance_criteria:
            message += f"Acceptance criteria: {', '.join(task.acceptance_criteria)}\n"
        if task.tags:
            message += f"Tags: {', '.join(task.tags)}\n"

        result = self.run(message, task_id=task.id)

        if result.error:
            logger.error(f"Ошибка декомпозиции задачи {task.id}: {result.error}")
            return []

        # Парсим JSON из ответа
        subtasks_data = self._parse_decomposition(result.response)
        if not subtasks_data:
            logger.error(f"Не удалось распарсить декомпозицию для {task.id}")
            return []

        # Создаём TaskCard для каждой подзадачи
        project_id = task.project_id or f"proj-{uuid.uuid4().hex[:6]}"
        workspace_path = f"workspace/{project_id}/"

        subtask_cards: list[TaskCard] = []
        subtask_ids: list[str] = []

        # Генерируем ID заранее для зависимостей
        for _ in subtasks_data:
            subtask_ids.append(_new_id())

        for i, st_data in enumerate(subtasks_data):
            # Преобразуем depends_on_index → depends_on task IDs
            deps = []
            for dep_idx in st_data.get("depends_on_index", []):
                if 0 <= dep_idx < len(subtask_ids) and dep_idx != i:
                    deps.append(subtask_ids[dep_idx])

            card = TaskCard(
                id=subtask_ids[i],
                project_id=project_id,
                parent_task_id=task.id,
                title=st_data.get("title", f"Subtask {i+1}"),
                description=st_data.get("description", ""),
                acceptance_criteria=st_data.get("acceptance_criteria", []),
                required_skills=st_data.get("required_skills", []),
                priority=st_data.get("priority", 5),
                status="planning",
                stage_folder="1_planning",
                workspace_path=workspace_path,
                depends_on=deps,
                tags=st_data.get("tags", []),
            )
            subtask_cards.append(card)

        # Обновляем родительскую задачу
        task.project_id = project_id
        task.subtask_ids = subtask_ids
        task.workspace_path = workspace_path
        task.cost += result.cost

        logger.info(
            f"[{task.id}] Декомпозиция: {len(subtask_cards)} подзадач, "
            f"стоимость: ${result.cost:.4f}"
        )
        return subtask_cards

    def process_inbox_task(self, task: TaskCard) -> list[TaskCard]:
        """
        Full pipeline: take task from inbox, decompose, and route subtasks.
        1. Move task to 1_planning
        2. Decompose into subtasks
        3. Save subtasks in 1_planning
        4. Move tasks without dependencies to 2_ready
        5. Return all subtask cards
        """
        if not self.state_machine:
            logger.error("StateMachine не инициализирован")
            return []

        # inbox → planning
        self.state_machine.transition(task, "1_planning")

        # Декомпозиция
        subtasks = self.decompose(task)
        if not subtasks:
            return []

        # Сохраняем подзадачи и маршрутизируем
        pipeline_root = self.state_machine.pipeline_root
        for st in subtasks:
            st.save(pipeline_root / "1_planning")

            # Задачи без зависимостей → сразу в 2_ready
            if not st.depends_on:
                self.state_machine.transition(st, "2_ready")

        # Обновляем родительскую задачу
        task.save(pipeline_root / "1_planning")

        return subtasks

    def _parse_decomposition(self, response: str) -> list[dict]:
        """Extract subtasks JSON from Claude's response."""
        # Ищем JSON-блок в ответе
        text = response.strip()

        # Попытка найти JSON в markdown-блоке ```json...```
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()

        # Ищем JSON-объект
        try:
            # Находим начало JSON
            json_start = text.index("{")
            json_end = text.rindex("}") + 1
            data = json.loads(text[json_start:json_end])
            return data.get("subtasks", [])
        except (ValueError, json.JSONDecodeError) as e:
            logger.error(f"JSON parse error: {e}")
            # Пытаемся парсить как массив
            try:
                json_start = text.index("[")
                json_end = text.rindex("]") + 1
                return json.loads(text[json_start:json_end])
            except (ValueError, json.JSONDecodeError):
                return []
