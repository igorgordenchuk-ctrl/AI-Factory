"""
SkillRegistry — loads and manages skill definitions from YAML files.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillDefinition:
    """Parsed skill definition from YAML."""
    skill_id: str
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    preferred_model: str = "claude-sonnet-4-6"
    cost_tier: str = "medium"  # low=haiku, medium=sonnet, high=opus


# Маппинг cost_tier → модель
TIER_MODELS = {
    "low": "claude-haiku-4-5-20251001",
    "medium": "claude-sonnet-4-6",
    "high": "claude-opus-4-6",
}


class SkillRegistry:
    """
    Loads skill YAML files from a directory.
    Provides lookup by skill_id.
    """

    def __init__(self, skills_dir: str | Path = "config/skills"):
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillDefinition] = {}
        self._load_all()

    def _load_all(self):
        """Load all .yaml files from skills directory."""
        if not self.skills_dir.exists():
            logger.warning(f"Папка навыков не найдена: {self.skills_dir}")
            return

        for yaml_file in sorted(self.skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not data or "skill_id" not in data:
                    continue
                skill = SkillDefinition(
                    skill_id=data["skill_id"],
                    name=data.get("name", data["skill_id"]),
                    description=data.get("description", ""),
                    system_prompt=data.get("system_prompt", ""),
                    tools=data.get("tools", []),
                    preferred_model=data.get("preferred_model", "claude-sonnet-4-6"),
                    cost_tier=data.get("cost_tier", "medium"),
                )
                self._skills[skill.skill_id] = skill
                logger.debug(f"Навык загружен: {skill.skill_id}")
            except Exception as e:
                logger.error(f"Ошибка загрузки навыка {yaml_file}: {e}")

        logger.info(f"Загружено навыков: {len(self._skills)}")

    def get(self, skill_id: str) -> SkillDefinition | None:
        """Get skill by ID."""
        return self._skills.get(skill_id)

    def get_many(self, skill_ids: list[str]) -> list[SkillDefinition]:
        """Get multiple skills by IDs."""
        return [s for sid in skill_ids if (s := self._skills.get(sid))]

    def all_skills(self) -> list[SkillDefinition]:
        """Return all loaded skills."""
        return list(self._skills.values())

    def all_ids(self) -> list[str]:
        """Return all skill IDs."""
        return list(self._skills.keys())

    def model_for_skills(self, skill_ids: list[str]) -> str:
        """Determine the best model based on skills' cost tiers (use highest tier)."""
        tiers = {"low": 0, "medium": 1, "high": 2}
        max_tier = "low"
        for sid in skill_ids:
            skill = self._skills.get(sid)
            if skill and tiers.get(skill.cost_tier, 0) > tiers.get(max_tier, 0):
                max_tier = skill.cost_tier
        return TIER_MODELS.get(max_tier, "claude-sonnet-4-6")

    def tools_for_skills(self, skill_ids: list[str]) -> list[str]:
        """Collect unique tool names from multiple skills."""
        tools = set()
        for sid in skill_ids:
            skill = self._skills.get(sid)
            if skill:
                tools.update(skill.tools)
        return sorted(tools)

    def prompt_for_skills(self, skill_ids: list[str]) -> str:
        """Combine system prompts from multiple skills."""
        parts = []
        for sid in skill_ids:
            skill = self._skills.get(sid)
            if skill and skill.system_prompt:
                parts.append(f"## Role: {skill.name}\n{skill.system_prompt}")
        return "\n\n".join(parts)
