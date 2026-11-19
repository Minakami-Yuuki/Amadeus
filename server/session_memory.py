"""Compact cross-session memory for the system prompt.

The alpha product sends a bounded window of the *current* session only, so a new
conversation starts blank. This module builds a short digest of the most recent
earlier sessions and lets ``llm.prompts`` append it, which keeps names, ongoing
topics and stated preferences available without a retrieval index or GPU cost.

It is a digest, not full recall: the newest few sessions, a few lines each, hard
character cap, cached briefly so a chat turn never reparses every file.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core import session_manager

DEFAULT_SESSIONS = 3
DEFAULT_LINES_PER_SESSION = 4
DEFAULT_MAX_CHARS = 900
DEFAULT_CACHE_SECONDS = 60.0

_HEADER = (
    "[過去の会話の記憶（参考）]\n"
    "以下は以前の会話の抜粋。呼称・話題・好みの連続性のために使うが、"
    "いまのユーザー発話より優先しない。ここに無いことを覚えているふりはしない。\n"
)

_cache: dict[str, object] = {"key": "", "at": 0.0, "value": ""}


def _enabled() -> bool:
    return str(os.environ.get("AMADEUS_SESSION_MEMORY", "1")).strip().lower() not in {"0", "false", "off", "no"}


def _session_files(session_dir: Path) -> list[Path]:
    try:
        files = [p for p in session_dir.glob("*.json") if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return files


def _tail_lines(dialog: list, limit: int) -> list[str]:
    lines: list[str] = []
    for entry in reversed(dialog):
        if len(lines) >= limit:
            break
        if not isinstance(entry, dict):
            continue
        text = " ".join(str(entry.get("content") or "").split()).strip()
        if not text:
            continue
        role = "ユーザー" if entry.get("role") == "user" else "アメデウス"
        lines.append(f"- {role}: {text[:120]}")
    lines.reverse()
    return lines


def _digest(session_dir: Path, current_session_id: str, sessions: int,
            lines_per_session: int, max_chars: int) -> str:
    collected: list[str] = []
    used = 0
    picked = 0
    for path in _session_files(session_dir):
        if picked >= sessions:
            break
        if path.stem == current_session_id:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dialog = payload.get("dialog")
        if not isinstance(dialog, list) or not dialog:
            continue
        block = _tail_lines(dialog, lines_per_session)
        if not block:
            continue
        for line in block:
            if used + len(line) > max_chars:
                break
            collected.append(line)
            used += len(line)
        picked += 1
    if not collected:
        return ""
    return _HEADER + "\n".join(collected)


def build_memory_digest(
    current_session_id: str | None = None,
    *,
    sessions: int = DEFAULT_SESSIONS,
    lines_per_session: int = DEFAULT_LINES_PER_SESSION,
    max_chars: int = DEFAULT_MAX_CHARS,
    cache_seconds: float = DEFAULT_CACHE_SECONDS,
) -> str:
    """Return the prompt block, or an empty string when there is nothing to say."""
    if not _enabled():
        return ""
    current = str(current_session_id or "")
    session_dir = Path(getattr(session_manager, "_SESSION_DIR", "") or "")
    if not session_dir or not session_dir.is_dir():
        return ""
    key = f"{current}|{sessions}|{lines_per_session}|{max_chars}"
    now = time.monotonic()
    if _cache.get("key") == key and now - float(_cache.get("at") or 0.0) < cache_seconds:
        return str(_cache.get("value") or "")
    try:
        value = _digest(session_dir, current, sessions, lines_per_session, max_chars)
    except Exception:
        value = ""
    _cache.update({"key": key, "at": now, "value": value})
    return value


def clear_cache() -> None:
    _cache.update({"key": "", "at": 0.0, "value": ""})
