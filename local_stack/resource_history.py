"""Bounded resource samples and gateway observations on one UTC time axis.

This module never polls, estimates token position, or attributes system-wide
utilization to a request. Gateway markers use flow observation timestamps, not
resource timestamps, inferred hop state, or reconstructed transition times.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping

METRICS = ("cpu", "gpu", "ram", "vram")
NAMES = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM", "vram": "VRAM"}
COLORS = {"cpu": "bright_cyan", "gpu": "bright_magenta", "ram": "bright_green", "vram": "bright_yellow"}
ACTIVE_STAGES = {
    "ingress": "v", "validated": "v", "load_or_validate": "v", "forward_wait": "w",
    "response_headers": "h", "streaming": "s", "first_content": "a", "benchmark": "b",
}
TERMINAL_STAGES = {"completed": "+", "failed": "×", "rejected": "×", "cancelled": "×"}
REQUEST_ID = re.compile(r"req-[1-9][0-9]{0,18}")
HISTORY_WINDOWS = (120, 300, 900)
# Bound storage independently of the visible window. Cover 15 minutes even at
# the terminal's fastest 0.5-second poll cadence.
HISTORY_POINTS = 1802


def utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _number(value, lower=0, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= lower and (upper is None or value <= upper) else None


@dataclass(frozen=True)
class ResourceSample:
    timestamp: datetime
    percentages: dict[str, float | None]
    used_gib: dict[str, float | None]
    total_gib: dict[str, float | None]
    cpu_interval_seconds: float | None = None
    segment: int = 0
    source: str = "gateway"


@dataclass(frozen=True)
class GatewayMark:
    timestamp: datetime
    stage: str


def normalize_sample(timestamp: datetime, resources: Mapping[str, Any], segment=0, source="gateway") -> ResourceSample:
    percentages = {"cpu": _number(resources.get("cpu_utilization_percent"), upper=100), "gpu": _number(resources.get("gpu_utilization_percent"), upper=100), "ram": None, "vram": None}
    used, totals = {"ram": None, "vram": None}, {"ram": None, "vram": None}
    for metric, total_key, free_key, divisor in (("ram", "total_ram_gib", "available_ram_gib", 1), ("vram", "total_vram_mib", "free_vram_mib", 1024)):
        total, free = _number(resources.get(total_key)), _number(resources.get(free_key))
        if total is not None and total > 0 and free is not None and free <= total:
            used[metric], totals[metric] = (total - free) / divisor, total / divisor
            percentages[metric] = (total - free) / total * 100
    return ResourceSample(timestamp, percentages, used, totals, _number(resources.get("cpu_interval_seconds")), segment, source)


def _bucket(point, end, window, columns):
    start = end - window
    if start <= point <= end:
        return min(columns - 1, int((point - start) / window * columns))
    return None


def _gateway_generation(status):
    generations = [status.get(key) for key in ("generation", "supervisor_generation") if status.get(key) is not None]
    if generations and all(isinstance(item, str) and 0 < len(item) <= 128 and item == generations[0] for item in generations):
        return generations[0]
    return None


class ResourceHistory:
    """An observation-only module: ingestion and drawing have no side effects."""
    def __init__(self, *, max_points=HISTORY_POINTS, window_seconds=HISTORY_WINDOWS[0], fresh_seconds=6):
        if type(max_points) is not int or max_points < 1 or window_seconds <= 0 or fresh_seconds <= 0:
            raise ValueError("History bounds must be positive.")
        self.samples: deque[ResourceSample] = deque(maxlen=max_points)
        self.window_seconds = float(window_seconds)
        self.fresh_seconds = float(fresh_seconds)
        self.observation = "unavailable"
        self.gateway_marks: deque[GatewayMark] = deque(maxlen=max_points)
        self.gateway_observation = "unavailable"
        self._generation: str | None = None
        self._old_generations: deque[str] = deque(maxlen=16)
        self._segment = 0
        self._gap = False
        self._flow_time: datetime | None = None
        self._flow_gap = True
        self._stages: OrderedDict[str, tuple[int, str]] = OrderedDict()

    def zoom(self) -> int:
        """Cycle display windows without changing retained observations or polling."""
        current = next((i for i, span in enumerate(HISTORY_WINDOWS) if span == self.window_seconds), -1)
        self.window_seconds = float(HISTORY_WINDOWS[(current + 1) % len(HISTORY_WINDOWS)])
        return int(self.window_seconds)

    def _resource_gap(self, reason):
        self.observation = reason
        self._gap = True
        return False

    def observe(self, status: Mapping[str, Any], *, connected: bool, now: datetime | None = None,
                system_sample: tuple[datetime, Mapping[str, Any]] | None = None) -> bool:
        """Accept independent source timestamps, never pair a stage to a metric sample."""
        if not connected:
            self._flow_gap = True
            self.gateway_observation = "offline"
            if system_sample is None:
                return self._resource_gap("offline")
            timestamp, resources = system_sample
            if not 0 <= ((now or datetime.now(timezone.utc)) - timestamp).total_seconds() <= self.fresh_seconds:
                return self._resource_gap("delayed system sample")
            return self._append_sample(timestamp, resources, "system")
        generation = _gateway_generation(status)
        if generation in self._old_generations:
            self.gateway_observation = "past generation"
            self._flow_gap = True
            return self._resource_gap("past generation")
        if generation is not None and generation != self._generation:
            # Never align a prior supervisor's history with new gateway observations.
            if self._generation is not None:
                self._old_generations.append(self._generation)
            self._generation = generation
            self.samples = deque((sample for sample in self.samples if sample.source == "system"), maxlen=self.samples.maxlen)
            self.gateway_marks.clear()
            self._stages.clear()
            self._flow_time = None
            self._flow_gap = True
        flow = status.get("request_flow")
        self._observe_gateway(flow, generation, now or datetime.now(timezone.utc))
        if self._generation is not None and (generation is None or isinstance(flow, dict) and flow.get("generation") != generation):
            return self._resource_gap("generation unverified")
        if status.get("resources_stale") is not False:
            return self._resource_gap("stale" if status.get("resources_stale") is True else "freshness unavailable")
        timestamp = utc_timestamp(status.get("resources_sampled_at"))
        if timestamp is None:
            return self._resource_gap("timestamp unavailable")
        if now is not None and not 0 <= (now - timestamp).total_seconds() <= self.fresh_seconds:
            return self._resource_gap("delayed sample")
        resources = status.get("resources")
        if not isinstance(resources, dict):
            return self._resource_gap("unavailable")
        return self._append_sample(timestamp, resources, "gateway")

    def _append_sample(self, timestamp, resources, source):
        prior = self.samples[-1] if self.samples else None
        if prior and timestamp <= prior.timestamp:
            # The gateway's independently timed first sample can precede the
            # last local reading. Keep that fresh reading until the next poll.
            if source == "gateway" and prior.source == "system":
                return False
            if source != prior.source or timestamp < prior.timestamp:
                return self._resource_gap("out-of-order sample")
            return False
        if self._gap or (prior and source != prior.source):
            self._segment += 1
            self._gap = False
        self.observation = "system-only sample" if source == "system" else "live sample"
        self.samples.append(normalize_sample(timestamp, resources, self._segment, source))
        return True

    def _observe_gateway(self, flow, generation, now):
        if not isinstance(flow, dict) or type(flow.get("version")) is not int or flow["version"] != 1:
            self._flow_gap = True
            self.gateway_observation = "unavailable"
            return
        if generation is None or flow.get("generation") != generation:
            self._flow_gap = True
            self.gateway_observation = "generation unverified"
            return
        timestamp = utc_timestamp(flow.get("observed_at"))
        if timestamp is None or not 0 <= (now - timestamp).total_seconds() <= self.fresh_seconds:
            self._flow_gap = True
            self.gateway_observation = "stale or missing"
            return
        if self._flow_time is not None and timestamp <= self._flow_time:
            if timestamp < self._flow_time:
                self._flow_gap = True
                self.gateway_observation = "out-of-order sample"
            return
        contiguous = not self._flow_gap and self._flow_time is not None and (timestamp - self._flow_time).total_seconds() <= self.fresh_seconds
        self._flow_time = timestamp
        self._flow_gap = False
        self.gateway_observation = "observed"
        # A newly attached view may inherit a very old terminal record. Seed it
        # without making its finish look like new activity after a polling gap.
        last = flow.get("last")
        self._stage_mark(last, TERMINAL_STAGES, timestamp, emit=contiguous)
        active = flow.get("active")
        for request in active[:64] if isinstance(active, list) else ():
            self._stage_mark(request, ACTIVE_STAGES, timestamp, emit=True)

    def _stage_mark(self, request, allowed, timestamp, *, emit):
        if not isinstance(request, dict) or request.get("source") != "observed":
            return
        request_id, stage, sequence = (request.get(k) for k in ("id", "stage", "event_seq"))
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            return
        if not isinstance(stage, str) or stage not in allowed or type(sequence) is not int or not 0 < sequence < 2**63:
            return
        if stage in TERMINAL_STAGES and request.get("outcome") != stage:
            return
        previous = self._stages.get(request_id)
        if previous is not None and (sequence <= previous[0] or previous[1] in TERMINAL_STAGES):
            return
        self._stages[request_id] = (sequence, stage)
        self._stages.move_to_end(request_id)
        if len(self._stages) > 128:
            self._stages.popitem(last=False)
        if emit and (previous is None or stage != previous[1]):
            self.gateway_marks.append(GatewayMark(timestamp, stage))

    def latest(self, now: datetime | None = None) -> ResourceSample | None:
        now = now or datetime.now(timezone.utc)
        if self.observation not in {"live sample", "system-only sample"} or not self.samples:
            return None
        age = (now - self.samples[-1].timestamp).total_seconds()
        return self.samples[-1] if 0 <= age <= self.fresh_seconds else None

    def series(self, metric: str, columns: int, now: datetime | None = None) -> tuple[float | None, ...]:
        if metric not in METRICS or type(columns) is not int or columns < 1:
            raise ValueError("Choose a known metric and positive chart width.")
        now = now or datetime.now(timezone.utc)
        end = now.timestamp()
        buckets: list[float | None] = [None] * columns
        previous = None
        previous_index = 0
        gap_columns = set()
        for sample in self.samples:
            index = _bucket(sample.timestamp.timestamp(), end, self.window_seconds, columns)
            if index is not None:
                if previous is not None and index <= previous_index + 1 and (
                    previous.segment != sample.segment
                    or (sample.timestamp - previous.timestamp).total_seconds() > self.fresh_seconds
                ):
                    gap_columns.add(previous_index)
                    buckets[previous_index] = None
                previous, previous_index = sample, index
                # Downsampling never hides a known missing value within a bucket.
                value = sample.percentages[metric]
                if value is None:
                    gap_columns.add(index)
                buckets[index] = None if index in gap_columns else value
        return tuple(buckets)

    def gateway_lane(self, columns: int, now: datetime | None = None) -> str:
        if type(columns) is not int or columns < 1:
            raise ValueError("Chart width must be positive.")
        end = (now or datetime.now(timezone.utc)).timestamp()
        cells = [" "] * columns
        for mark in self.gateway_marks:
            index = _bucket(mark.timestamp.timestamp(), end, self.window_seconds, columns)
            if index is not None:
                marker = ACTIVE_STAGES.get(mark.stage) or TERMINAL_STAGES[mark.stage]
                cells[index] = marker if cells[index] == " " else "*"
        return "".join(cells)

    def gateway_status(self, now: datetime) -> str:
        if self.gateway_observation == "observed" and (self._flow_time is None or not 0 <= (now - self._flow_time).total_seconds() <= self.fresh_seconds):
            return "stale"
        return self.gateway_observation

    def label(self, metric: str, now: datetime | None = None) -> str:
        sample = self.latest(now)
        if sample is None or sample.percentages[metric] is None:
            return "unavailable"
        percentage = sample.percentages[metric]
        if metric in ("ram", "vram"):
            return f"{sample.used_gib[metric]:.2f}/{sample.total_gib[metric]:.2f} GiB ({percentage:.1f}%)"
        interval = sample.cpu_interval_seconds if metric == "cpu" else None
        return f"{percentage:.1f}%" + (f" (Δ{interval:g}s)" if interval is not None else "")

    def status_label(self, now: datetime | None = None) -> str:
        if self.latest(now) is None and self.observation in {"live sample", "system-only sample"}:
            return "stale system-only sample" if self.observation == "system-only sample" else "stale sample"
        return self.observation


def sparkline(values):
    levels = "▁▂▃▄▅▆▇█"
    return "".join(" " if value is None else levels[min(7, int(value / 100 * 7))] for value in values)


def bar_rows(values, height=3):
    """0–100% vertical scale; a missing value draws no column at all."""
    if height < 1:
        raise ValueError("A chart needs at least one row.")
    blocks = " ▁▂▃▄▅▆▇█"
    heights = [None if value is None else max(1, round(value / 100 * height * 8)) for value in values]
    return [
        "".join(" " if steps is None else blocks[min(8, max(0, steps - 8 * row))] for steps in heights)
        for row in range(height - 1, -1, -1)
    ]


def plot_rows(values, height=3):
    """Draw samples on a 0–100% dotted grid; never join across unknown time buckets."""
    if height < 1:
        raise ValueError("A chart needs at least one row.")
    levels = height * 4 - 1
    cells = [[0] * len(values) for _ in range(height)]
    dots = ((1, 2, 4, 64), (8, 16, 32, 128))
    def draw(column, half, low, high):
        for y in range(min(low, high), max(low, high) + 1):
            cells[y // 4][column] |= dots[half][y % 4]

    levels_by_column = [None if value is None else round((100 - value) / 100 * levels) for value in values]
    for column, y in enumerate(levels_by_column):
        if y is None:
            continue
        draw(column, 0, y, y)
        if column + 1 < len(values) and (next_y := levels_by_column[column + 1]) is not None:
            midpoint = round((y + next_y) / 2)
            draw(column, 1, y, midpoint)
            draw(column + 1, 0, midpoint, next_y)
    return tuple(tuple(chr(0x2800 + cell) if cell else "·" for cell in row) for row in cells)


def render_resource_history(history: ResourceHistory, width=110, height=None, *, now: datetime | None = None):
    """Rich graphs, but no telemetry collection; tests inject a fixed UTC clock."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    now = now or datetime.now(timezone.utc)
    span = f"−{history.window_seconds:g}s"
    status = history.status_label(now)
    latest = history.latest(now)
    age = f" · {(now - latest.timestamp).total_seconds():.1f}s ago" if latest else ""
    legend = Text(f"SYSTEM-WIDE · {status}{age} · not request attribution", style="dim", no_wrap=True, overflow="ellipsis")
    gw_status = history.gateway_status(now)
    key = Text("GW at poll: v ingress/load · w wait · h headers · s stream · a text · b bench · + done · × other end · * multi", style="dim", no_wrap=True, overflow="ellipsis")
    if width < 110 or (height is not None and height < 30):
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=5)
        table.add_column(width=min(29, max(12, width // 3)))
        table.add_column(ratio=1)
        chart_width = max(6, width - 5 - min(29, max(12, width // 3)) - 9)
        for metric in METRICS:
            table.add_row(Text(NAMES[metric], style=COLORS[metric]), Text(history.label(metric, now), no_wrap=True, overflow="ellipsis"), Text(sparkline(history.series(metric, chart_width, now)), style=COLORS[metric], no_wrap=True))
        axis = Text(f"0–100% scale · {span} → now ({now:%H:%M:%S} UTC) · blanks = gaps", style="dim", no_wrap=True, overflow="ellipsis")
        table.add_row(Text("GW", style="bold cyan"), Text(gw_status + " at poll", style="dim", no_wrap=True), Text(history.gateway_lane(chart_width, now), style="cyan", no_wrap=True))
        if height is not None and height < 24:
            scope = "SYSTEM ONLY" if "system-only" in status else "SYSTEM-WIDE"
            short_axis = Text(f"{scope}{age} · 0–100% · {span} → now · gaps=unknown", style="dim", no_wrap=True, overflow="ellipsis")
            return Panel(Group(table, short_axis), title="SYSTEM HISTORY · H returns", border_style="cyan")
        compact_key = Text("GW: v load · w wait · h hdr · s stream · a text · b bench · + × end · * multi", style="dim", no_wrap=True, overflow="ellipsis")
        return Panel(Group(table, axis, compact_key, legend), title="SYSTEM HISTORY · H returns", border_style="cyan")
    chart_width = max(8, (width - 2) // 2 - 11)
    panels = []
    for metric in METRICS:
        values = history.series(metric, chart_width, now)
        graph = bar_rows(values, 3)
        body = Text(no_wrap=True)
        for label, row in zip(("100 ┤", " 50 ┤", "  0 ┤"), graph):
            body.append(label, style="dim")
            body.append(row, style=COLORS[metric])
            body.append("\n")
        body.append(" GW ┤", style="dim")
        body.append(history.gateway_lane(chart_width, now), style="bright_cyan")
        body.append(f"\n     {span}" + " " * max(1, chart_width - len(span) - 3) + "now", style="dim")
        panels.append(Panel(body, title=Text(NAMES[metric] + " · " + history.label(metric, now)), border_style=COLORS[metric], padding=(0, 1)))
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1, width=(width - 2) // 2)
    grid.add_column(ratio=1, width=(width - 2) // 2)
    grid.add_row(panels[0], panels[1])
    grid.add_row(panels[2], panels[3])
    legend.append(f" · blanks = gaps · GW {gw_status}")
    return Group(grid, key, legend)
