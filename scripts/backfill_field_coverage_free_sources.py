#!/usr/bin/env python3
"""CLI wrapper for free-source field coverage backfill."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def _main() -> int:
    from src.data_pipeline.field_coverage_backfill import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
