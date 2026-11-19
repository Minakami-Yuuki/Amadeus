"""Strip a repeated opening greeting from stored assistant replies.

A model can lock onto whatever opener its own history shows ("おはよう、" in the
sample session) and repeat it every turn. Removing it from the stored replies
both repairs the transcript and stops the history from anchoring the next turn.

Only a greeting that repeats in at least --min occurrences is touched, and only
as a prefix; the rest of the reply is preserved verbatim.

Usage:
    python tools/strip_repeated_opener.py            # dry run
    python tools/strip_repeated_opener.py --apply    # rewrite with .bak backups
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_DIR = PROJECT_ROOT / "sessions"

GREETINGS = ("おはよう", "こんにちは", "こんばんは", "おやすみ", "ただいま")
PREFIX_RE = re.compile(r"^\s*(?:" + "|".join(GREETINGS) + r")\s*[、,，。!！~～\s]*")


def dialog_of(payload: dict) -> list:
    for key in ("dialog", "messages", "history"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def assistant_text(entry: dict) -> str:
    return str(entry.get("content") or entry.get("text") or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strip a repeated greeting opener from assistant replies.")
    parser.add_argument("--apply", action="store_true", help="rewrite files (backs up to .bak)")
    parser.add_argument("--dir", default=str(DEFAULT_SESSION_DIR), help="session directory")
    parser.add_argument("--min", type=int, default=3, help="minimum repeats before stripping")
    args = parser.parse_args(argv)

    root = Path(args.dir)
    if not root.is_dir():
        print(f"no session directory at {root}")
        return 1

    changed_files = 0
    stripped_total = 0
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skip {path.name}: {exc}")
            continue
        entries = dialog_of(payload)
        counters: collections.Counter = collections.Counter()
        for entry in entries:
            if str(entry.get("role")) != "assistant":
                continue
            match = PREFIX_RE.match(assistant_text(entry))
            if match:
                counters[match.group(0).strip(" 、,，")] += 1
        if not counters:
            continue
        opener, count = counters.most_common(1)[0]
        if count < args.min:
            print(f"{path.name}: opener {opener!r} x{count} (below --min {args.min})")
            continue
        print(f"{path.name}: opener {opener!r} repeats {count}x")
        stripped = 0
        for entry in entries:
            if str(entry.get("role")) != "assistant":
                continue
            text = assistant_text(entry)
            fixed = PREFIX_RE.sub("", text, count=1)
            if fixed != text and fixed.strip():
                key = "content" if "content" in entry else "text"
                entry[key] = fixed.strip()
                stripped += 1
        changed_files += 1
        stripped_total += stripped
        if args.apply:
            for key in ("dialog", "messages", "history"):
                if isinstance(payload.get(key), list):
                    break
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not changed_files:
        print("nothing to strip")
        return 0
    print(f"{'stripped' if args.apply else 'would strip'} {stripped_total} replies in {changed_files} session(s)")
    if not args.apply:
        print("dry run: add --apply to rewrite (a .bak copy is written first)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
