"""
AgentFactory — creates agent instances based on required skills.
Manages agent registry (registry/agents.json).
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

from core.skill_registry import SkillRegistry
from core.token_tracker import TokenTracker

logger = logging.getLogger(__name__)


class AgentFactory:
    """
    Creates Worker agents from skill definitions.
    Tracks all created agents in registry/agents.json.
    Reuses idle agents with matching skills when possible.
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        token_tracker: TokenTracker,
        registry_path: str | Path = "registry/agents.json",
        default_worker_model: str = "",
    ):
        self.skill_registry = skill_registry
        self.token_tracker = token_tracker
        self.registry_path = Path(registry_path)
        self.default_worker_model = default_worker_model
        self._agents: dict[str, dict] = {}  # agent_id → info
        self._lock = Lock()
        self._load_registry()

    def _load_registry(self):
        """Load agent registry from disk."""
        if self.registry_path.exists():
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            for agent in data.get("agents", []):
                self._agents[agent["agent_id"]] = agent

    def _save_registry(self):
        """Save agent registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "agents": list(self._agents.values()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def create_worker(
        self,
        required_skills: list[str],
        agent_name: Optional[str] = None,
    ):
        """
        Create a Worker agent with specified skills.
        Returns a Worker instance.
        """
        from core.worker import Worker

        agent_id = f"worker-{uuid.uuid4().hex[:6]}"
        if not agent_name:
            skill_names = [
                s.name for s in self.skill_registry.get_many(required_skills)
            ]
            agent_name = " & ".join(skill_names) if skill_names else "Worker"

        # Определяем модель по навыкам
        model = self.skill_registry.model_for_skills(required_skills)

        worker = Worker(
            agent_id=agent_id,
            name=agent_name,
            skills=required_skills,
            skill_registry=self.skill_registry,
            model=model,
            token_tracker=self.token_tracker,
        )

        # Регистрация
        with self._lock:
            self._agents[agent_id] = {
                "agent_id": agent_id,
                "name": agent_name,
                "type": "worker",
                "skills": required_skills,
                "model": model,
                "status": "idle",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "tasks_completed": 0,
            }
            self._save_registry()

        logger.info(f"Создан агент: {agent_id} ({agent_name}) навыки={required_skills}")
        return worker

    def get_or_create_worker(self, required_skills: list[str]):
        """Reuse idle worker with matching skills, or create new one."""
        with self._lock:
            for agent_id, info in self._agents.items():
                if (
                    info["type"] == "worker"
                    and info["status"] == "idle"
                    and set(info["skills"]) == set(required_skills)
                ):
                    info["status"] = "busy"
                    self._save_registry()
                    logger.info(f"Переиспользуем агента: {agent_id}")

                    from core.worker import Worker

                    return Worker(
                        agent_id=agent_id,
                        name=info["name"],
                        skills=required_skills,
                        skill_registry=self.skill_registry,
                        model=info["model"],
                        token_tracker=self.token_tracker,
                    )

        return self.create_worker(required_skills)

    def update_status(self, agent_id: str, status: str):
        """Update agent status in registry."""
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["status"] = status
                self._save_registry()

    def increment_completed(self, agent_id: str):
        """Increment tasks_completed counter."""
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id]["tasks_completed"] += 1
                self._save_registry()

    def get_all_agents(self) -> list[dict]:
        """Return all agent info."""
        with self._lock:
            return list(self._agents.values())

    def get_active_count(self) -> int:
        """Count busy agents."""
        with self._lock:
            return sum(1 for a in self._agents.values() if a["status"] == "busy")
