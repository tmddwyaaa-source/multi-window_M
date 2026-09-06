"""Acceptance checks for bump(). Prints RESULT PASS on success."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.counter import bump  # noqa: E402


def main() -> int:
    assert bump(0) == 1
    assert bump(41) == 42
    try:
        bump(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative n must raise ValueError")
    try:
        bump(True)  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("bool must raise TypeError (bool is not a plain int)")
    try:
        bump(1.5)  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("float must raise TypeError")
    print("RESULT PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RESULT FAIL: {exc}")
        raise SystemExit(1)
