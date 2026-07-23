"""Evolution review system for self-improvement.

After each conversation, analyzes the interaction for patterns worth learning
and persists learnings to a file. These learnings are injected into future
conversations via the system prompt.

Based on ha-claw's evolution_review.py but adapted for mimo_auto's architecture.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Configuration
_LESSONS_FILE = "/data/mimocode/lessons.json"
_MAX_LESSONS = 100
_REVIEW_TTL = 3600  # 1 hour between reviews of same conversation
_MAX_RECENT_REVIEWS = 50

# System prompt for the AI to analyze conversations
_REVIEW_SYSTEM_PROMPT = (
    "后台进化回顾。\n"
    "这不是用户对话，而是内部学习分析。\n\n"
    "分析以下对话，提取可复用的经验：\n"
    "1. 用户纠正了你的风格、语气、格式 → 记录教训\n"
    "2. 用户纠正了工作流程 → 记录正确步骤\n"
    "3. 出现了新的技术/修复/调试路径 → 记录方法\n"
    "4. 某个技能有误/过时 → 记录需要修正的内容\n\n"
    "返回 JSON 格式：\n"
    '{"lessons": ["教训1", "教训2"], "category": "style|workflow|technique|correction"}\n'
    "如果没有值得记录的内容，返回：\n"
    '{"lessons": [], "category": "none"}'
)


class EvolutionReview:
    """Manages conversation review and learning persistence."""

    def __init__(self) -> None:
        self._lessons: list[dict[str, Any]] = []
        self._recent_reviews: dict[str, float] = {}
        self._load_lessons()

    def _load_lessons(self) -> None:
        """Load persisted lessons from file."""
        try:
            if os.path.exists(_LESSONS_FILE):
                with open(_LESSONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._lessons = data[-_MAX_LESSONS:]  # Keep only recent
        except Exception as err:
            _LOGGER.debug("Failed to load lessons: %s", err)
            self._lessons = []

    def _save_lessons(self) -> None:
        """Persist lessons to file."""
        try:
            os.makedirs(os.path.dirname(_LESSONS_FILE), exist_ok=True)
            with open(_LESSONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._lessons[-_MAX_LESSONS:], f, ensure_ascii=False, indent=2)
        except Exception as err:
            _LOGGER.warning("Failed to save lessons: %s", err)

    def _should_review(self, original_text: str, response_text: str) -> bool:
        """Determine if a conversation should be reviewed."""
        # Skip empty or very short exchanges
        if len(original_text.strip()) < 5 or len(response_text.strip()) < 10:
            return False

        # Skip if reviewed recently (fingerprint-based dedup)
        fingerprint = hashlib.sha256(
            f"{original_text[:200]}|{response_text[:200]}".encode()
        ).hexdigest()[:16]

        now = time.time()
        if fingerprint in self._recent_reviews:
            if now - self._recent_reviews[fingerprint] < _REVIEW_TTL:
                return False

        self._recent_reviews[fingerprint] = now

        # Prune old reviews
        cutoff = now - _REVIEW_TTL * 2
        self._recent_reviews = {
            k: v for k, v in self._recent_reviews.items() if v > cutoff
        }
        if len(self._recent_reviews) > _MAX_RECENT_REVIEWS:
            ordered = sorted(self._recent_reviews.items(), key=lambda x: x[1])
            self._recent_reviews = dict(ordered[-_MAX_RECENT_REVIEWS:])

        return True

    async def schedule_review(
        self,
        original_text: str,
        response_text: str,
        mimo_client: Any,
        session_id: str,
    ) -> None:
        """Schedule a background review of the conversation.

        Uses a SEPARATE session to avoid polluting the user's conversation context.

        Args:
            original_text: User's message.
            response_text: AI's response.
            mimo_client: MimoAIClient instance for sending review prompt.
            session_id: Session ID for the user (NOT used for review).
        """
        if not self._should_review(original_text, response_text):
            return

        try:
            review_prompt = (
                f"用户消息：{original_text[:500]}\n\n"
                f"AI 回复：{response_text[:500]}\n\n"
                f"请分析这次对话，提取可复用的经验。"
            )

            # Create a dedicated review session (not the user's session)
            review_session_id = await mimo_client.ensure_session("")
            if not review_session_id:
                _LOGGER.debug("Failed to create review session, skipping")
                return

            # Send review prompt to mimo serve (fire and forget)
            asyncio.create_task(
                self._run_review(mimo_client, review_session_id, review_prompt)
            )
        except Exception as err:
            _LOGGER.debug("Failed to schedule review: %s", err)

    async def _run_review(
        self,
        mimo_client: Any,
        session_id: str,
        review_prompt: str,
    ) -> None:
        """Run the evolution review in background."""
        try:
            response = await mimo_client.send_message(
                review_prompt,
                session_id,
                system=_REVIEW_SYSTEM_PROMPT,
            )

            if not response:
                return

            # Try to parse JSON response
            try:
                # Find JSON in response (may have surrounding text)
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    data = json.loads(response[start:end])
                    lessons = data.get("lessons", [])
                    category = data.get("category", "none")

                    if lessons and category != "none":
                        for lesson in lessons:
                            self._add_lesson(lesson, category)
                        _LOGGER.info(
                            "Evolution review: captured %d lessons (category=%s)",
                            len(lessons), category,
                        )
            except (json.JSONDecodeError, ValueError):
                # Response is not JSON, skip
                pass

        except Exception as err:
            _LOGGER.debug("Evolution review failed: %s", err)

    def _add_lesson(self, lesson: str, category: str) -> None:
        """Add a lesson to the store."""
        # Dedup: check if similar lesson already exists
        lesson_lower = lesson.lower().strip()
        for existing in self._lessons:
            if existing.get("text", "").lower().strip() == lesson_lower:
                return

        self._lessons.append({
            "text": lesson,
            "category": category,
            "timestamp": time.time(),
        })

        # Keep only recent lessons
        if len(self._lessons) > _MAX_LESSONS:
            self._lessons = self._lessons[-_MAX_LESSONS:]

        self._save_lessons()

    def get_lessons_context(self) -> str:
        """Get lessons as context string for system prompt injection."""
        if not self._lessons:
            return ""

        lines = ["以下是你之前学到的经验："]
        for lesson in self._lessons[-10:]:  # Last 10 lessons
            lines.append(f"- {lesson['text']}")

        return "\n".join(lines)

    def get_lessons_count(self) -> int:
        """Get the number of stored lessons."""
        return len(self._lessons)


# Singleton instance
_review: EvolutionReview | None = None


def get_evolution_review() -> EvolutionReview:
    """Get or create the singleton EvolutionReview."""
    global _review
    if _review is None:
        _review = EvolutionReview()
    return _review
