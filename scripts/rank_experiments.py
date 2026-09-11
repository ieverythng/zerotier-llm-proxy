#!/usr/bin/env python3
"""Rank experiment CSV rows by a chosen metric.

The autoresearch workflow keeps a normalized CSV ledger.  This helper turns
one or more such CSVs into a compact Markdown ranking without assuming a
Watson-specific schema beyond common metric column names.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable


DEFAULT_METRICS = (
    "prompt_per_second",
    "task_elapsed_s",
    "elapsed_s",
    "completion_tok_s",
    "total_tok_s",
)


def parse_float(value: object) -> float | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def read_rows(paths: Iterable[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                row = dict(row)
                row.setdefault("source_file", str(path))
                rows.append(row)
    return rows


def choose_metric(rows: list[dict[str, str]], explicit: str | None) -> str:
    if explicit:
        return explicit
    for metric in DEFAULT_METRICS:
        if any(parse_float(row.get(metric)) is not None for row in rows):
            return metric
    raise SystemExit(
        "No metric column found. Pass --metric or include one of: "
        + ", ".join(DEFAULT_METRICS)
    )


def direction_for_metric(metric: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    lowered = metric.lower()
    if "elapsed" in lowered or "latency" in lowered or "ms" in lowered:
        return "min"
    return "max"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="+", type=Path, help="CSV ledger(s) to rank")
    parser.add_argument("--metric", help="Metric column to rank")
    parser.add_argument("--direction", choices=("max", "min"), help="Optimization direction")
    parser.add_argument("--top", type=int, default=20, help="Maximum rows to print")
    args = parser.parse_args()

    rows = read_rows(args.csv)
    if not rows:
        raise SystemExit("No rows found")

    metric = choose_metric(rows, args.metric)
    direction = direction_for_metric(metric, args.direction)
    ranked = [
        (parse_float(row.get(metric)), row)
        for row in rows
        if parse_float(row.get(metric)) is not None
    ]
    ranked.sort(key=lambda item: item[0], reverse=(direction == "max"))

    print(f"# Experiment ranking by `{metric}` ({direction})")
    print()
    print("| Rank | Step | Decision | Variant | Metric | Target | Elapsed s | Prompt n | Source |")
    print("|---:|---:|---|---|---:|---:|---:|---:|---|")
    for index, (value, row) in enumerate(ranked[: args.top], start=1):
        print(
            "| {rank} | {step} | {decision} | {variant} | {metric:.3f} | {target} | "
            "{elapsed} | {prompt_n} | `{source}` |".format(
                rank=index,
                step=row.get("step", ""),
                decision=row.get("decision") or row.get("status", ""),
                variant=row.get("variant", ""),
                metric=value,
                target=row.get("target") or row.get("prompt_tokens_target", ""),
                elapsed=row.get("elapsed_s", ""),
                prompt_n=row.get("prompt_n") or row.get("tokens_evaluated", ""),
                source=row.get("source") or row.get("source_file", ""),
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
