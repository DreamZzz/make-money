#!/usr/bin/env python3
"""Run research-only alpha gate reports for candidate factors."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.research.alpha_gate import evaluate_alpha_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate research alpha candidates against the shared gate."
    )
    parser.add_argument(
        "--metrics-json",
        required=True,
        help="Path to a metrics JSON object produced by a validation script.",
    )
    args = parser.parse_args(argv)

    metrics = json.loads(Path(args.metrics_json).read_text())
    result = evaluate_alpha_gate(metrics)
    payload = {
        "passed": result.passed,
        "failed_reasons": result.failed_reasons,
        "metrics": result.metrics,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
