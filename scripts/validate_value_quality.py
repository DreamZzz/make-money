#!/usr/bin/env python3
"""CLI wrapper for value-quality standalone validation."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

def _main() -> int:
    from src.research.strategies.value_quality_validation import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
