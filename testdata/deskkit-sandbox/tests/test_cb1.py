"""Acceptance checks for data/words-batch/CB1.json. Prints RESULT PASS on success."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "words-batch" / "CB1.json"


def main() -> int:
    if not PATH.is_file():
        raise AssertionError(f"missing {PATH.relative_to(ROOT).as_posix()}")
    data = json.loads(PATH.read_text(encoding="utf-8"))
    if data.get("window") != "CB1":
        raise AssertionError('field "window" must be the file token CB1, not the chat window id')
    if data.get("owner_window") != "C1":
        raise AssertionError('field "owner_window" must be C1 (the real window id)')
    words = data.get("words")
    if not isinstance(words, list) or not (8 <= len(words) <= 12):
        raise AssertionError("words must be a list of 8 to 12 items")
    for index, item in enumerate(words):
        if not isinstance(item, dict):
            raise AssertionError(f"words[{index}] must be an object")
        en = item.get("en")
        zh = item.get("zh")
        if not isinstance(en, str) or not en.strip():
            raise AssertionError(f"words[{index}].en missing")
        if not isinstance(zh, str) or not zh.strip():
            raise AssertionError(f"words[{index}].zh missing")
    print("RESULT PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RESULT FAIL: {exc}")
        raise SystemExit(1)
