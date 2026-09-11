#!/usr/bin/env python3
"""Render an autoresearch-style HTML trace from a normalized results CSV.

The output intentionally has no external dependencies: it embeds a small SVG
line plot, a best-so-far trace, and the full keep/reject/repeat/control ledger.
"""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


GOOD_DECISIONS = {"keep", "baseline", "promote"}
REPEAT_DECISIONS = {"repeat", "needs-repeat", "measure", "control"}
BAD_DECISIONS = {"discard", "reject", "crash", "fail", "failed"}


def parse_float(value: object) -> float | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_int(value: object, default: int) -> int:
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def load_rows(path: Path, metric: str) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    rows.sort(key=lambda row: parse_int(row.get("step"), len(rows)))
    return rows


def decision_class(row: dict[str, str]) -> str:
    decision = (row.get("decision") or row.get("status") or "").strip().lower()
    status = (row.get("status") or "").strip().lower()
    if decision in BAD_DECISIONS or status in BAD_DECISIONS:
        return "discard"
    if decision in GOOD_DECISIONS or status in GOOD_DECISIONS:
        return "keep"
    if decision in REPEAT_DECISIONS or status in REPEAT_DECISIONS:
        return "repeat"
    return "repeat"


def best_series(values: list[float], direction: str) -> list[float]:
    out: list[float] = []
    best: float | None = None
    for value in values:
        if best is None:
            best = value
        elif direction == "max":
            best = max(best, value)
        else:
            best = min(best, value)
        out.append(best)
    return out


def pct_delta(first: float, best: float, direction: str) -> float:
    if first == 0:
        return 0.0
    if direction == "max":
        return ((best - first) / abs(first)) * 100.0
    return ((first - best) / abs(first)) * 100.0


def render_svg(rows: list[dict[str, str]], metric: str, direction: str) -> str:
    plot_rows = [row for row in rows if parse_float(row.get(metric)) is not None]
    values = [parse_float(row.get(metric)) or 0.0 for row in plot_rows]
    raw_best = best_series(values, direction)
    y_values = values + raw_best
    ymin, ymax = min(y_values), max(y_values)
    if ymin == ymax:
        ymin -= 1.0
        ymax += 1.0
    pad = (ymax - ymin) * 0.10
    ymin -= pad
    ymax += pad

    width, height = 1120, 430
    left, right, top, bottom = 70, 1090, 28, 364
    span_x = max(1, len(plot_rows) - 1)

    def x_at(index: int) -> float:
        return left + (right - left) * index / span_x

    def y_at(value: float) -> float:
        return bottom - (bottom - top) * ((value - ymin) / (ymax - ymin))

    def path(points: list[float]) -> str:
        return " ".join(
            ("M" if index == 0 else "L") + f"{x_at(index):.1f},{y_at(value):.1f}"
            for index, value in enumerate(points)
        )

    lines: list[str] = [
        '<svg viewBox="0 0 1120 430" role="img" aria-label="Autoresearch optimization trace">'
    ]
    for tick in range(5):
        value = ymin + (ymax - ymin) * tick / 4
        y = y_at(value)
        lines.append(
            f"<line x1='{left}' x2='{right}' y1='{y:.1f}' y2='{y:.1f}' class='grid'/>"
            f"<text x='60' y='{y+4:.1f}' text-anchor='end'>{value:.0f}</text>"
        )
    lines.append(f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" class="axis"/>')
    lines.append(f'<line x1="{left}" x2="{left}" y1="{top}" y2="{bottom}" class="axis"/>')
    lines.append(f'<path d="{path(values)}" class="raw"/>')
    lines.append(f'<path d="{path(raw_best)}" class="best"/>')

    for index, row in enumerate(plot_rows):
        value = values[index]
        klass = decision_class(row)
        label = row.get("variant") or f"step-{index}"
        target = row.get("target") or row.get("prompt_tokens_target") or ""
        title = html.escape(f"{label} @ {target}: {value:.3f}")
        lines.append(
            f"<circle class='{klass}' cx='{x_at(index):.1f}' cy='{y_at(value):.1f}' r='7'>"
            f"<title>{title}</title></circle>"
        )
    for index, row in enumerate(plot_rows):
        if index not in {0, 1, 3, len(plot_rows) - 1}:
            continue
        value = values[index]
        target = row.get("target") or row.get("prompt_tokens_target") or ""
        label = html.escape(f"{row.get('variant', '')} @ {target}")
        y = y_at(value) - 12
        lines.append(f"<text x='{x_at(index):.1f}' y='{y:.1f}' text-anchor='middle'>{label}</text>")
    lines.append(f'<text x="{left}" y="408">experiment step</text>')
    lines.append(f'<text x="18" y="46" transform="rotate(-90 18,46)">{html.escape(metric)}</text>')
    lines.append("</svg>")
    return "\n    ".join(lines)


def render_table(rows: list[dict[str, str]], metric: str) -> str:
    body = []
    for row in rows:
        klass = decision_class(row)
        decision = html.escape(row.get("decision") or row.get("status") or "")
        source = html.escape(Path(row.get("source", "")).name or row.get("source", ""))
        body.append(
            "<tr>"
            f"<td>{html.escape(row.get('step', ''))}</td>"
            f"<td><span class='dot {klass}'></span>{decision}</td>"
            f"<td>{html.escape(row.get(metric, ''))}</td>"
            f"<td>{html.escape(row.get('variant', ''))}</td>"
            f"<td>{html.escape(row.get('target', ''))}</td>"
            f"<td>{html.escape(row.get('elapsed_s', ''))}</td>"
            f"<td>{html.escape(row.get('prompt_n', ''))}</td>"
            f"<td><code>{source}</code></td>"
            "</tr>"
        )
    return "\n".join(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Normalized results CSV")
    parser.add_argument("--output", required=True, type=Path, help="HTML output path")
    parser.add_argument("--title", default="Autoresearch Trace")
    parser.add_argument("--metric", default="prompt_per_second")
    parser.add_argument("--direction", choices=("max", "min"), default="max")
    parser.add_argument("--subtitle", default="")
    parser.add_argument(
        "--append-html",
        default="",
        help="Optional HTML fragment appended after the ledger table",
    )
    args = parser.parse_args()

    rows = load_rows(args.input, args.metric)
    metric_rows = [row for row in rows if parse_float(row.get(args.metric)) is not None]
    if not metric_rows:
        raise SystemExit(f"No rows with metric {args.metric!r} in {args.input}")

    values = [parse_float(row.get(args.metric)) or 0.0 for row in metric_rows]
    best_value = max(values) if args.direction == "max" else min(values)
    first_value = values[0]
    delta = pct_delta(first_value, best_value, args.direction)

    title = html.escape(args.title)
    subtitle = html.escape(args.subtitle)
    svg = render_svg(rows, args.metric, args.direction)
    table = render_table(rows, args.metric)
    appended = f"  {args.append_html}\n" if args.append_html else ""

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: dark; --bg:#080b12; --panel:#101827; --line:#26364f; --text:#eaf2ff; --muted:#9fb0c7; --ok:#6ee7b7; --warn:#fbbf24; --bad:#fb7185; --blue:#75e6ff; }}
    body {{ margin:0; background:radial-gradient(circle at 10% 0%,#18233a 0,#080b12 38%,#05070b 100%); color:var(--text); font-family:Inter,Segoe UI,system-ui,sans-serif; line-height:1.55; }}
    main {{ max-width:1220px; margin:0 auto; padding:36px 22px 80px; }}
    h1 {{ font-size:clamp(34px,5vw,60px); line-height:1.02; letter-spacing:-.045em; margin:12px 0; }}
    h2 {{ margin-top:34px; }}
    .lead {{ color:#c4d2e8; font-size:18px; max-width:900px; }}
    .gridcards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin:20px 0; }}
    .card {{ background:linear-gradient(180deg,var(--panel),#0d1421); border:1px solid var(--line); border-radius:18px; padding:18px; box-shadow:0 14px 40px #0005; }}
    .kpi {{ font-size:32px; font-weight:850; letter-spacing:-.04em; }}
    .muted {{ color:var(--muted); }}
    svg {{ width:100%; height:auto; background:#0b1220; border:1px solid var(--line); border-radius:18px; }}
    .grid {{ stroke:#213049; stroke-width:1; }}
    .axis {{ stroke:#51647f; stroke-width:1.5; }}
    .raw {{ fill:none; stroke:#75e6ff88; stroke-width:2; }}
    .best {{ fill:none; stroke:#6ee7b7; stroke-width:3; }}
    circle {{ stroke:#07101e; stroke-width:3; }}
    circle.keep {{ fill:var(--ok); }}
    circle.repeat {{ fill:var(--warn); }}
    circle.discard {{ fill:var(--muted); }}
    text {{ fill:#cfe3ff; font-size:12px; }}
    table {{ width:100%; border-collapse:collapse; background:#0c1320; border:1px solid var(--line); border-radius:14px; overflow:hidden; }}
    th,td {{ border-bottom:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top; }}
    th {{ color:#b8d7ff; background:#121e31; text-transform:uppercase; font-size:12px; }}
    code {{ color:#d9ecff; }}
    .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:7px; }}
    .dot.keep {{ background:var(--ok); }} .dot.repeat {{ background:var(--warn); }} .dot.discard {{ background:var(--muted); }}
  </style>
</head>
<body>
<main>
  <h1>{title}</h1>
  <p class="lead">{subtitle}</p>
  <div class="gridcards">
    <div class="card"><div class="muted">Optimized metric</div><div class="kpi">{html.escape(args.metric)}</div><div class="muted">{'higher' if args.direction == 'max' else 'lower'} is better</div></div>
    <div class="card"><div class="muted">Best observed</div><div class="kpi">{best_value:.3f}</div><div class="muted">metric value</div></div>
    <div class="card"><div class="muted">Improvement from first point</div><div class="kpi">{delta:+.1f}%</div><div class="muted">search metric delta</div></div>
    <div class="card"><div class="muted">Experiments recorded</div><div class="kpi">{len(rows)}</div><div class="muted">ledger rows, {len(metric_rows)} plotted</div></div>
  </div>
  {svg}
  <h2>Experiment ledger</h2>
  <table>
    <tr><th>Step</th><th>Decision</th><th>{html.escape(args.metric)}</th><th>Variant</th><th>Target</th><th>Elapsed s</th><th>Prompt n</th><th>Artifact</th></tr>
    {table}
  </table>
{appended}</main>
</body>
</html>
"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
