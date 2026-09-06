"""Acceptance checks for greet(). Prints RESULT PASS on success."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.greet import greet  # noqa: E402


def main() -> int:
    assert greet("Ada") == "Hello, Ada!"
    assert greet("M2") == "Hello, M2!"
    try:
        greet("  ")
    except ValueError:
        pass
    else:
        raise AssertionError("blank name must raise ValueError")
    try:
        greet("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty name must raise ValueError")
    print("RESULT PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RESULT FAIL: {exc}")
        raise SystemExit(1)
