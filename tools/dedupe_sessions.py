"""Report (and optionally repair) duplicated entries in stored chat sessions.

Only provable duplicates are removed: entries whose (turn_id, role, content) was
already written earlier in the same dialog. Genuine repeats by the user carry a
new turn_id and are always kept.

Usage:
    python tools/dedupe_sessions.py            # dry run, prints findings
    python tools/dedupe_sessions.py --apply    # rewrite with .bak backups
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_DIR = Path(PROJECT_ROOT / "sessions")


def dialog_of(payload: dict) -> list:
    for key in ("dialog", "messages", "history"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def dedupe(entries: list) -> tuple[list, list]:
    kept: list = []
    seen: set = set()
    removed: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        turn_id = str(entry.get("turn_id") or "")
        role = str(entry.get("role") or "")
        content = str(entry.get("content") or entry.get("text") or "")
        if turn_id:
            key = (turn_id, role, content)
            if key in seen:
                removed.append(entry)
                continue
            seen.add(key)
        kept.append(entry)
    return kept, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove duplicated session dialog entries.")
    parser.add_argument("--apply", action="store_true", help="rewrite files (backs up to .bak)")
    parser.add_argument("--dir", default=str(DEFAULT_SESSION_DIR), help="session directory")
    args = parser.parse_args(argv)

    root = Path(args.dir)
    if not root.is_dir():
        print(f"no session directory at {root}")
        return 1

    total_removed = 0
    touched = 0
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skip {path.name}: {exc}")
            continue
        entries = dialog_of(payload)
        if not entries:
            continue
        kept, removed = dedupe(entries)
        if not removed:
            continue
        touched += 1
        total_removed += len(removed)
        print(f"{path.name}: {len(entries)} -> {len(kept)} entries ({len(removed)} duplicate)")
        for entry in removed[:3]:
            text = str(entry.get("content") or entry.get("text") or "")[:60]
            print(f"    - {entry.get('role')} {text!r}")
        if not args.apply:
            continue
        for key in ("dialog", "messages", "history"):
            if isinstance(payload.get(key), list):
                payload[key] = kept
                break
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not touched:
        print("no duplicates found")
        return 0
    print(f"{'removed' if args.apply else 'would remove'} {total_removed} entries in {touched} session(s)")
    if not args.apply:
        print("dry run: add --apply to rewrite (a .bak copy is written first)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
