"""Persona management for MiMo HA 管家.

Stores and loads user-defined persona (name, role, tone, etc.)
and injects it into the system prompt.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Default persona file path
_PERSONA_FILE = "/data/mimocode/persona.json"

# Default persona
DEFAULT_PERSONA = {
    "name": "灵犀",
    "role": "Home Assistant 管家",
    "tone": "友好、简洁",
    "language": "中文",
    "owner": "主人",
    "custom": "",
}


class PersonaStore:
    """Manages AI persona configuration."""

    def __init__(self) -> None:
        self._persona: dict[str, Any] = DEFAULT_PERSONA.copy()
        self._load()

    def _load(self) -> None:
        """Load persona from file."""
        try:
            if os.path.exists(_PERSONA_FILE):
                with open(_PERSONA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._persona.update(data)
        except Exception as err:
            _LOGGER.debug("Failed to load persona: %s", err)

    def _save(self) -> None:
        """Save persona to file."""
        try:
            os.makedirs(os.path.dirname(_PERSONA_FILE), exist_ok=True)
            with open(_PERSONA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._persona, f, ensure_ascii=False, indent=2)
        except Exception as err:
            _LOGGER.warning("Failed to save persona: %s", err)

    def get_persona(self) -> dict[str, Any]:
        """Get current persona."""
        return self._persona.copy()

    def set_persona(self, persona: dict[str, Any]) -> None:
        """Update persona and save."""
        self._persona.update(persona)
        self._save()

    def get_name(self) -> str:
        """Get AI name."""
        return self._persona.get("name", "灵犀")

    def get_owner(self) -> str:
        """Get owner name."""
        return self._persona.get("owner", "主人")

    def build_persona_prompt(self) -> str:
        """Build persona section for system prompt."""
        name = self.get_name()
        role = self._persona.get("role", "Home Assistant 管家")
        tone = self._persona.get("tone", "友好、简洁")
        owner = self.get_owner()
        custom = self._persona.get("custom", "")

        lines = [
            f"你的名字是 {name}，你是{owner}的{role}。",
            f"你的语气是{tone}。",
            f"请用中文回复。",
        ]

        if custom:
            lines.append(f"额外说明：{custom}")

        return "\n".join(lines)


# Singleton instance
_persona_store: PersonaStore | None = None


def get_persona_store() -> PersonaStore:
    """Get or create the singleton PersonaStore."""
    global _persona_store
    if _persona_store is None:
        _persona_store = PersonaStore()
    return _persona_store
