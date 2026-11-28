"""Curated historical projections of published, bounded Watson qualifications.

No registry/status lookup, file reads, subprocesses or readiness decisions.
Keep report citations and distinguish model phases, first text, and wall time.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Qualification:
    scenario: str
    route: str
    allocation: int
    actual_input: int | None
    actual_output: int | None
    prefill_tps: float | None
    decode_tps: float | None
    first_text_s: float | None
    wall_s: float | None
    minimum_free_vram_mib: float | None
    outcome: str
    report: str


_HISTORICAL_TEXT = "docs/reports/watson-historical-text-baselines.md"
_INKLING_150K = "docs/reports/watson-inkling-150k-2026-11-25.md"

QUALIFICATIONS = (
    Qualification("Inkling 100K text", "2026-11-24 · native b12651, previous text-only profile", 100096,
                  98048, 256, 305.09, 54.38, 321.45, None, 2540,
                  "Cold text timing; not the CPU-projector or routed profile.", _HISTORICAL_TEXT + "#inkling-100k-text"),
    Qualification("Inkling 100K text recall", "2026-11-24 · native, separate synthetic chat", 100096,
                  98021, 183, None, None, None, 325.39, 2528,
                  "Three markers and missing/null; natural stop.", _HISTORICAL_TEXT + "#inkling-100k-recall"),
    Qualification("Inkling 100K mixed recall", "2026-11-25 · native CPU projector, isolated", 100096,
                  89605, 196, 317.32, 55.65, None, 285.95, 2132.0,
                  "Three markers, missing/null and pixel-only OCR; natural stop. No streamed first-text timestamp.",
                  _INKLING_150K + "#mixed-text-and-image-pressure-pass-at-100k"),
    Qualification("Inkling 100K routed vision", "2026-11-25 · Watson → Headroom → LiteLLM → llama.cpp", 100096,
                  None, None, None, None, None, None, 2212.9,
                  "Three generated image/OCR cases passed (3.34, 1.99, 1.80 s wall); no DSH image-delivery proof.",
                  _INKLING_150K + "#100k-main-profile-qualification"),
    Qualification("Inkling 150K timing", "2026-11-25 · native b12651, guarded experiment", 150016,
                  147968, 256, 247.83, 47.82, 597.11, 602.45, 1096.8,
                  "Completed under experimental floors; below normal 2,048 MiB VRAM reserve.", _INKLING_150K + "#150k-timing-pass"),
    Qualification("Inkling 150K text recall", "2026-11-25 · native, separate guarded chat", 150016,
                  139694, 313, None, None, None, 560.52, 1276.9,
                  "Three markers and missing/null; natural stop. Not 150K vision or serving qualification.",
                  _INKLING_150K + "#150k-long-chat-recall-pass"),
    Qualification("Inkling 150K mixed recall", "2026-11-25 · native CPU projector, guarded experiment", 150016,
                  139742, 323, None, None, None, 556.42, 1343.09,
                  "Five fields correct; below normal VRAM reserve. Subsequently selected as the explicit experimental default.",
                  _INKLING_150K + "#150k-mixed-vision-and-recall-pass"),
    Qualification("Inkling 150K default quick", "2026-11-25 · native Observatory vision-quick, selected profile", 150016,
                  1024, 64, 491.76, 70.72, 2.078, 2.969, 1548,
                  "Seven synthetic cases passed. Timing is short-input; minimum free is across the whole suite.",
                  _INKLING_150K + "#default-rollout-and-observatory-acceptance"),
    Qualification("Inkling 200K capacity", "2026-11-25 · native CPU projector, tight-fitter experiment", 200192,
                  None, None, None, None, None, None, 679.44,
                  "Three tiny image/OCR cases passed only. No near-200K prompt or long-decode qualification; not default.",
                  _INKLING_150K + "#200k-capacity-probe"),
    Qualification("Saluki 150K text", "2026-10-09 · historical Saluki stack, separate model", 150016,
                  142834, 32, 667.8, 29.63, 216.10, None, 2618,
                  "Distinct route/workload; not a like-for-like comparison with Inkling.", _HISTORICAL_TEXT + "#saluki-150k-text"),
)
