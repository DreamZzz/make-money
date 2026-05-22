"""Validate research-only cross-sectional alpha candidates with the shared gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from src.data_pipeline.loader import get_connection
from src.research.alpha_validation import (
    SUPPORTED_CANDIDATES,
    run_research_candidate_grid,
    run_research_candidate_validation,
)
from src.research.strategies.value_quality_validation import (
    _load_benchmark_suite,
    load_latest_alpha158_portfolio_returns,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a research-only alpha candidate against the shared promotion gate.",
    )
    parser.add_argument("--candidate", required=True, choices=sorted(SUPPORTED_CANDIDATES))
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--country", default="CN")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--buffer-n", type=int, default=None, help="Keep prior holdings while still inside this rank buffer.")
    parser.add_argument("--max-replacements", type=int, default=None, help="Maximum voluntary replacements per rebalance.")
    parser.add_argument("--holding-days", type=int, default=20)
    parser.add_argument("--rebalance-freq", choices=["daily", "weekly", "monthly", "quarterly"], default="monthly")
    parser.add_argument("--benchmark", default="MIXED_EQUAL")
    parser.add_argument("--lookback", type=int, default=None, help="Optional lookback window passed to the candidate scorer.")
    parser.add_argument("--smooth-days", type=int, default=None, help="Optional smoothing window passed to the candidate scorer.")
    parser.add_argument("--size-neutral", action="store_true", help="Enable size-neutral residual scoring when supported.")
    parser.add_argument("--beta-neutral", action="store_true", help="Enable beta-neutral residual scoring when supported.")
    parser.add_argument("--beta-lookback", type=int, default=None, help="Rolling beta lookback passed to the candidate scorer.")
    parser.add_argument("--grid", action="store_true", help="Run a parameter grid and print ranked results.")
    parser.add_argument("--lookbacks", default="20,60,120", help="Comma-separated lookbacks for --grid.")
    parser.add_argument("--smooth-days-list", default="1,3,5,10", help="Comma-separated smoothing windows for --grid.")
    parser.add_argument("--beta-lookbacks", default="60,90,120", help="Comma-separated beta lookbacks for --grid.")
    parser.add_argument("--top-ns", default="20,50,80", help="Comma-separated top_n values for --grid.")
    parser.add_argument("--buffer-ns", default="none,80,160,300,500", help="Comma-separated buffer_n values for --grid; use none for no buffer.")
    parser.add_argument("--max-replacements-list", default="none,1,2,3", help="Comma-separated max replacement values for --grid; use none for no cap.")
    parser.add_argument("--holding-days-list", default="20", help="Comma-separated holding_days values for --grid.")
    parser.add_argument("--rebalance-freqs", default="monthly,quarterly", help="Comma-separated rebalance frequencies for --grid.")
    parser.add_argument("--fail-on-gate", action="store_true", help="Return exit code 2 when alpha gate fails.")
    args = parser.parse_args(argv)
    score_kwargs = {}
    if args.lookback is not None:
        score_kwargs["lookback"] = args.lookback
    if args.smooth_days is not None:
        score_kwargs["smooth_days"] = args.smooth_days
    if args.size_neutral:
        score_kwargs["size_neutral"] = True
    if args.beta_neutral:
        score_kwargs["beta_neutral"] = True
        if args.beta_lookback is not None:
            score_kwargs["beta_lookback"] = args.beta_lookback

    conn = get_connection(read_only=True)
    try:
        benchmark_suite = _load_benchmark_suite(conn)
        reference_returns, reference_experiment_id = load_latest_alpha158_portfolio_returns(
            conn,
            args.start,
            args.end,
        )
        if args.grid:
            result = {
                "candidate": args.candidate,
                "decision_scope": "research_only",
                "benchmark_name": args.benchmark,
                "reference_experiment_id": reference_experiment_id,
                "results": run_research_candidate_grid(
                    conn=conn,
                    candidate=args.candidate,
                    start=args.start,
                    end=args.end,
                    country=args.country,
                    lookbacks=_parse_optional_ints(args.lookbacks),
                    smooth_days_list=_parse_optional_ints(args.smooth_days_list),
                    size_neutral_options=[False, True] if args.size_neutral else [False],
                    beta_neutral_options=[False, True] if args.beta_neutral else [False],
                    beta_lookbacks=_parse_optional_ints(args.beta_lookbacks),
                    top_ns=_parse_ints(args.top_ns),
                    buffer_ns=_parse_optional_ints(args.buffer_ns),
                    max_replacements_list=_parse_optional_ints(args.max_replacements_list),
                    holding_days_list=_parse_ints(args.holding_days_list),
                    rebalance_freqs=_parse_strings(args.rebalance_freqs),
                    benchmark_name=args.benchmark,
                    benchmark_returns=benchmark_suite.get(args.benchmark),
                    reference_returns=reference_returns,
                ),
            }
        else:
            result = run_research_candidate_validation(
                conn=conn,
                candidate=args.candidate,
                start=args.start,
                end=args.end,
                country=args.country,
                top_n=args.top_n,
                buffer_n=args.buffer_n,
                max_replacements_per_rebalance=args.max_replacements,
                holding_days=args.holding_days,
                rebalance_freq=args.rebalance_freq,
                benchmark_name=args.benchmark,
                benchmark_returns=benchmark_suite.get(args.benchmark),
                reference_returns=reference_returns,
                score_kwargs=score_kwargs,
            )
    finally:
        conn.close()

    if not args.grid:
        result["reference_experiment_id"] = reference_experiment_id
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if args.fail_on_gate and not _gate_passed(result):
        return 2
    if args.grid:
        return 0 if result.get("results") else 1
    return 0 if result.get("metrics") is not None else 1


def _gate_passed(result: dict) -> bool:
    if "results" in result:
        return any(item.get("alpha_gate_passed") for item in result["results"])
    return bool(result.get("alpha_gate_passed"))


def _parse_strings(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _parse_ints(value: str) -> list[int]:
    return [int(item) for item in _parse_strings(value)]


def _parse_optional_ints(value: str) -> list[int | None]:
    parsed: list[int | None] = []
    for item in _parse_strings(value):
        parsed.append(None if item.lower() in {"none", "null", "-"} else int(item))
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
