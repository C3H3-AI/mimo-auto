"""Feishu interactive card support.

Parses card specs from AI replies and builds Feishu Schema 2.0 cards
with clickable buttons that route back to the AI for action execution.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Card JSON pattern
_CARD_JSON_RE = re.compile(r"^\{.*\}$", re.DOTALL)

# Simple card syntax: "text | button1, button2 | button3"
_SIMPLE_CARD_RE = re.compile(r"^.+\|.+")

# Style mapping
_STYLE_MAP: dict[str, int] = {
    "gray": 0, "grey": 0,
    "blue": 1,
    "recommend": 2,
    "red": 3,
    "primary": 4,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
}

# Style suffix for simple syntax: "button text@blue"
_STYLE_SUFFIX_RE = re.compile(r"^(.+?)@(\w+)$")

# Limits
_MAX_KEYBOARD_ROWS = 5
_MAX_BUTTONS_PER_ROW = 5
_REPLY_MAX_LENGTH = 1800


@dataclass(slots=True)
class CardButton:
    """A single button in a card."""
    label: str
    data: str
    style: int = 1  # 0=gray, 1=blue, 2=recommend, 3=red, 4=primary
    visited_label: str = ""


@dataclass(slots=True)
class CardRow:
    """A row of buttons in a card."""
    buttons: list[CardButton] = field(default_factory=list)


@dataclass(slots=True)
class CardSpec:
    """Full card specification parsed from AI reply."""
    text: str
    rows: list[CardRow] = field(default_factory=list)
    card_id: str = ""


def parse_card_source(source: str) -> CardSpec | None:
    """Parse card source text into a CardSpec.

    Supports two formats:
    1. JSON: {"text": "...", "rows": [[{"label": "...", "data": "..."}]]}
    2. Simple: "text | button1, button2 | button3"
    """
    source = source.strip()
    if not source:
        return None

    if _CARD_JSON_RE.match(source):
        return _parse_json_card(source)

    if _SIMPLE_CARD_RE.match(source):
        return _parse_simple_card(source)

    return None


def _parse_json_card(source: str) -> CardSpec | None:
    """Parse JSON format card."""
    try:
        data = json.loads(source)
    except (json.JSONDecodeError, ValueError):
        return None

    text = str(data.get("text", ""))
    card_id = str(data.get("id", ""))
    rows: list[CardRow] = []

    for row_data in data.get("rows", data.get("buttons", [])):
        if isinstance(row_data, list):
            buttons = [_parse_button(b) for b in row_data if isinstance(b, (dict, str))]
        elif isinstance(row_data, dict) and "buttons" in row_data:
            buttons = [_parse_button(b) for b in row_data["buttons"] if isinstance(b, (dict, str))]
        elif isinstance(row_data, (dict, str)):
            buttons = [_parse_button(row_data)]
        else:
            continue

        buttons = [b for b in buttons if b is not None]
        if buttons:
            rows.append(CardRow(buttons=buttons))

    return CardSpec(text=text, rows=rows, card_id=card_id) if rows else None


def _parse_button(item: dict | str) -> CardButton | None:
    """Parse a single button definition."""
    if isinstance(item, str):
        return CardButton(label=item, data=item)

    label = str(item.get("label", item.get("text", "")))
    data = str(item.get("data", item.get("value", label)))
    style = int(item.get("style", 1))
    visited = str(item.get("visited_label", item.get("visited", f"已选: {label}")))

    return CardButton(label=label, data=data, style=style, visited_label=visited) if label else None


def _parse_simple_button(raw: str) -> CardButton | None:
    """Parse a button from simple syntax: 'label@style' or 'label=data'."""
    raw = raw.strip()
    if not raw:
        return None

    style = 1
    m = _STYLE_SUFFIX_RE.match(raw)
    if m:
        raw, color = m.group(1).strip(), m.group(2).strip().lower()
        style = _STYLE_MAP.get(color, 1)

    if "=" in raw:
        label, data = raw.split("=", 1)
        return CardButton(label=label.strip(), data=data.strip(), style=style)

    return CardButton(label=raw, data=raw, style=style)


def _parse_simple_card(source: str) -> CardSpec | None:
    """Parse simple format: 'text | button1, button2 | button3'."""
    lines = source.strip().split("|")
    if len(lines) < 2:
        return None

    text = lines[0].strip()
    rows: list[CardRow] = []

    for part in lines[1:]:
        buttons = [_parse_simple_button(b) for b in part.split(",")]
        buttons = [b for b in buttons if b is not None]
        if buttons:
            rows.append(CardRow(buttons=buttons))

    return CardSpec(text=text, rows=rows) if rows else None


# ---------------------------------------------------------------------------
# Feishu Card Building (Schema 2.0)
# ---------------------------------------------------------------------------

def build_feishu_card(spec: CardSpec, *, title: str = "MiMo 管家") -> dict:
    """Build a Feishu Schema 2.0 interactive card from a CardSpec."""
    elements: list[dict] = []

    # Add text content
    if spec.text:
        elements.append({"tag": "markdown", "content": spec.text[:_REPLY_MAX_LENGTH]})

    # Add button rows
    for row in spec.rows[:_MAX_KEYBOARD_ROWS]:
        columns: list[dict] = []
        for btn in row.buttons[:_MAX_BUTTONS_PER_ROW]:
            color = _STYLE_MAP.get(btn.style, "default")
            columns.append({
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": btn.label},
                    "type": color,
                    "width": "fill",
                    "value": {"action": btn.data},
                }],
            })
        if columns:
            elements.append({
                "tag": "column_set",
                "flex_mode": "bisect",
                "columns": columns,
            })

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "body": {
            "elements": elements,
        },
    }


def build_response_card(text: str, *, title: str = "MiMo 管家") -> dict:
    """Build a simple response card without buttons."""
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": text[:_REPLY_MAX_LENGTH]},
            ],
        },
    }


def should_send_as_card(text: str) -> bool:
    """Determine if text should be sent as a card (vs plain text)."""
    return (
        len(text) > 300
        or "\n1." in text
        or "##" in text
        or "###" in text
    )
