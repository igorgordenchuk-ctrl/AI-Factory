"""
TaskCard — единица работы в пайплайне AI Factory.
JSON-файл, который перемещается между папками pipeline/.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


def _new_id() -> str:
    return f"task-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewNote(BaseModel):
    """Single review entry from Supervisor."""
    attempt: int = 0
    score: float = 0.0
    notes: list[str] = Field(default_factory=list)
    verdict: str = ""  # APPROVED | REWORK | ESCALATED
    timestamp: str = Field(default_factory=_now)


class TaskCard(BaseModel):
    """Core data model — the task card that flows through the pipeline."""

    # Identity
    id: str = Field(default_factory=_new_id)
    project_id: str = ""
    parent_task_id: str = ""

    # Content
    title: str = ""
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)

    # Assignment
    required_skills: list[str] = Field(default_factory=list)
    assigned_agent_id: str = ""
    priority: int = 5  # 1-10, 1 = highest

    # State
    status: str = "new"  # new, planning, ready, in_progress, review, rework, completed, archived
    stage_folder: str = "0_inbox"
    attempt: int = 0
    max_attempts: int = 3

    # Input / Output
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    workspace_path: str = ""

    # Dependencies
    depends_on: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    subtask_ids: list[str] = Field(default_factory=list)

    # Review
    review_notes: list[ReviewNote] = Field(default_factory=list)
    review_score: float = 0.0

    # Tracking
    created_at: str = Field(default_factory=_now)
    started_at: str = ""
    completed_at: str = ""
    cost: float = 0.0
    tokens_used: dict = Field(default_factory=dict)

    # Metadata
    tags: list[str] = Field(default_factory=list)

    # --- Persistence ---

    def save(self, folder: Path) -> Path:
        """Save task card as JSON file in the given folder."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "TaskCard":
        """Load task card from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def list_in_folder(cls, folder: Path) -> list["TaskCard"]:
        """Load all task cards from a folder."""
        folder = Path(folder)
        if not folder.exists():
            return []
        cards = []
        for f in sorted(folder.glob("task-*.json")):
            if not f.name.endswith(".lock"):
                cards.append(cls.load(f))
        # Сортировка по приоритету (1 = самый важный)
        cards.sort(key=lambda c: c.priority)
        return cards
