#!/usr/bin/env python3
"""Watson stack benchmark harness for Phase 1/2 validation.

Dependency-free OpenAI-compatible checks for:
- endpoint health (/models plus optional Headroom health)
- decode throughput at selected synthetic context sizes
- tool-fidelity style JSON/action selection
- optional Lazarus logs/config metadata supplied by caller

Writes JSON and Markdown reports under reports/benchmarks/.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "benchmarks"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 900, headers: dict | None = None) -> tuple[dict | list | str, float, int | None]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            elapsed = time.time() - start
            try:
                return json.loads(raw), elapsed, resp.status
            except json.JSONDecodeError:
                return raw, elapsed, resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:4000]
        return {"error": f"HTTPError {e.code}", "body": body}, time.time() - start, e.code
    except Exception as e:  # noqa: BLE001 - harness should record failures, not crash early
        return {"error": repr(e)}, time.time() - start, None


def synthetic_context(target_tokens: int) -> str:
    if target_tokens <= 0:
        return ""
    # Rough 4 chars/token. Include deterministic markers so context loss is detectable.
    line = "CTX_MARKER_ALPHA benchmark operational note for Watson local context sweep. "
    target_chars = target_tokens * 4
    chunks: list[str] = []
    total = 0
    i = 0
    while total < target_chars:
        chunk = f"[{i:06d}] {line}"
        chunks.append(chunk)
        total += len(chunk)
        i += 1
    chunks.append(" CTX_MARKER_OMEGA final retained marker before the actual task. ")
    return "".join(chunks)


def chat_completion(base_url: str, model: str, user: str, system: str = "You are a concise benchmark assistant.", max_tokens: int = 96, temperature: float = 0.0, timeout: int = 900) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data, elapsed, status = http_json("POST", f"{base_url.rstrip('/')}/chat/completions", payload, timeout=timeout)
    row: dict = {"status": status, "elapsed_s": round(elapsed, 3), "raw_ok": not (isinstance(data, dict) and data.get("error"))}
    if isinstance(data, dict) and "choices" in data:
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {}) or {}
        completion_tokens = usage.get("completion_tokens") or max(1, len(content) // 4)
        row.update(
            {
                "content": content,
                "content_preview": content[:500],
                "usage": usage,
                "completion_tokens_est": completion_tokens,
                "completion_tok_s_est": round(completion_tokens / max(elapsed, 0.001), 3),
            }
        )
    else:
        row["error_payload"] = data
    return row


def run_endpoint_checks(base_url: str, headroom_url: str | None) -> list[dict]:
    rows = []
    data, elapsed, status = http_json("GET", f"{base_url.rstrip('/')}/models", timeout=15)
    rows.append({"kind": "endpoint", "name": "models", "url": f"{base_url.rstrip('/')}/models", "status": status, "elapsed_s": round(elapsed, 3), "ok": status == 200, "data_preview": str(data)[:1000]})
    if headroom_url:
        data, elapsed, status = http_json("GET", headroom_url, timeout=15)
        ok = status == 200 and not (isinstance(data, dict) and data.get("status") not in (None, "healthy"))
        rows.append({"kind": "endpoint", "name": "headroom", "url": headroom_url, "status": status, "elapsed_s": round(elapsed, 3), "ok": ok, "data_preview": str(data)[:1000]})
    return rows


def run_context_tests(base_url: str, model: str, contexts: list[int], max_tokens: int, timeout: int) -> list[dict]:
    rows = []
    for ctx in contexts:
        print(f"context test target={ctx} max_tokens={max_tokens}", flush=True)
        prompt = (
            synthetic_context(ctx)
            + "\n\nTask: Reply with exactly one JSON object with keys alpha_seen, omega_seen, summary. "
            + "alpha_seen and omega_seen must be booleans indicating whether CTX_MARKER_ALPHA and CTX_MARKER_OMEGA were present."
        )
        row = chat_completion(base_url, model, prompt, max_tokens=max_tokens, timeout=timeout)
        row.update({"kind": "context", "target_context_tokens": ctx, "prompt_chars": len(prompt)})
        content = row.get("content", "")
        row["marker_alpha_reported"] = "alpha" in content.lower() and "true" in content.lower()
        row["marker_omega_reported"] = "omega" in content.lower() and "true" in content.lower()
        rows.append(row)
    return rows


def run_tool_fidelity(base_url: str, model: str, timeout: int) -> list[dict]:
    prompt = """
You are inside a tool-calling harness, but tools are simulated. Choose exactly one tool call for this request and return only JSON.
Available tools:
- read_file(path: string)
- terminal(command: string)
- web_search(query: string)
Request: inspect the local file reports/research/local-model-inventory.md.
Return schema: {"tool": string, "arguments": object, "reason": string}
""".strip()
    row = chat_completion(base_url, model, prompt, system="Return strict JSON only. No markdown.", max_tokens=160, timeout=timeout)
    row.update({"kind": "tool_fidelity", "name": "select_read_file"})
    try:
        parsed = json.loads(row.get("content", ""))
        row["json_valid"] = True
        row["tool_correct"] = parsed.get("tool") == "read_file" and "local-model-inventory.md" in str(parsed.get("arguments", {}))
        row["parsed"] = parsed
    except Exception as e:  # noqa: BLE001
        row["json_valid"] = False
        row["tool_correct"] = False
        row["parse_error"] = repr(e)
    return [row]


def command_snapshot() -> dict:
    info = {}
    commands = {
        "git_head": ["git", "rev-parse", "--short", "HEAD"],
        "git_branch": ["git", "branch", "--show-current"],
    }
    for key, cmd in commands.items():
        try:
            info[key] = subprocess.check_output(cmd, cwd=ROOT, stderr=subprocess.DEVNULL, text=True, timeout=10).strip()
        except Exception as e:  # noqa: BLE001
            info[key] = f"ERROR {e!r}"
    return info


def summarize(results: dict) -> str:
    lines = [
        f"# Watson Stack Harness Report — {results['label']}",
        "",
        f"Generated: `{results['timestamp']}`",
        f"Base URL: `{results['base_url']}`",
        f"Model: `{results['model']}`",
        f"Git: `{results['environment'].get('git_branch')}` @ `{results['environment'].get('git_head')}`",
        "",
        "## Endpoint checks",
        "",
        "| Check | Status | Elapsed s | OK |",
        "|---|---:|---:|---|",
    ]
    for r in results["results"]:
        if r.get("kind") == "endpoint":
            lines.append(f"| {r['name']} | {r.get('status')} | {r.get('elapsed_s')} | {r.get('ok')} |")
    lines += ["", "## Context / throughput", "", "| Target ctx | Status | Elapsed s | Est completion tok/s | Alpha | Omega |", "|---:|---:|---:|---:|---|---|"]
    for r in results["results"]:
        if r.get("kind") == "context":
            lines.append(
                f"| {r.get('target_context_tokens')} | {r.get('status')} | {r.get('elapsed_s')} | {r.get('completion_tok_s_est')} | {r.get('marker_alpha_reported')} | {r.get('marker_omega_reported')} |"
            )
    lines += ["", "## Tool fidelity", "", "| Test | JSON valid | Tool correct | Elapsed s |", "|---|---|---|---:|"]
    for r in results["results"]:
        if r.get("kind") == "tool_fidelity":
            lines.append(f"| {r.get('name')} | {r.get('json_valid')} | {r.get('tool_correct')} | {r.get('elapsed_s')} |")
    failures = [r for r in results["results"] if r.get("ok") is False or r.get("raw_ok") is False or r.get("tool_correct") is False]
    lines += ["", "## Acceptance", ""]
    if failures:
        lines.append(f"- Result: **CHECK FAIL / NEEDS ATTENTION** ({len(failures)} failed rows).")
    else:
        lines.append("- Result: **PASS** for all executed rows.")
    context_rows = [r for r in results["results"] if r.get("kind") == "context" and r.get("completion_tok_s_est")]
    if context_rows:
        speeds = [float(r["completion_tok_s_est"]) for r in context_rows]
        lines.append(f"- Decode speed median across context rows: `{statistics.median(speeds):.3f}` est tok/s.")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.getenv("WATSON_HARNESS_BASE_URL", "http://172.24.16.1:8080/v1"))
    ap.add_argument("--model", default=os.getenv("WATSON_HARNESS_MODEL", "qwen36-turbo-hermes"))
    ap.add_argument("--headroom-url", default=os.getenv("WATSON_HEADROOM_URL", "http://172.24.16.1:8787/health"))
    ap.add_argument("--contexts", default="0,2048,8192", help="Comma-separated synthetic context token targets")
    ap.add_argument("--max-tokens", type=int, default=96)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--skip-context", action="store_true")
    ap.add_argument("--skip-tool-fidelity", action="store_true")
    ap.add_argument("--force", action="store_true", help="Run generation tests even if /models health check fails")
    args = ap.parse_args()

    contexts = [int(x.strip()) for x in args.contexts.split(",") if x.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "._-" else "_" for c in args.label)
    json_out = OUT_DIR / f"watson-stack-harness-{safe_label}-{stamp}.json"
    md_out = OUT_DIR / f"watson-stack-harness-{safe_label}-{stamp}.md"

    results = {
        "label": args.label,
        "timestamp": now_iso(),
        "base_url": args.base_url.rstrip("/"),
        "model": args.model,
        "contexts": contexts,
        "environment": command_snapshot(),
        "results": [],
    }
    results["results"].extend(run_endpoint_checks(args.base_url.rstrip("/"), args.headroom_url or None))
    model_check_ok = any(r.get("kind") == "endpoint" and r.get("name") == "models" and r.get("ok") for r in results["results"])
    if not model_check_ok and not args.force:
        results["results"].append(
            {
                "kind": "harness",
                "name": "generation_skipped",
                "ok": False,
                "reason": "/models endpoint failed; use --force to run generation tests anyway",
            }
        )
    if not args.skip_context and (model_check_ok or args.force):
        results["results"].extend(run_context_tests(args.base_url.rstrip("/"), args.model, contexts, args.max_tokens, args.timeout))
    if not args.skip_tool_fidelity and (model_check_ok or args.force):
        results["results"].extend(run_tool_fidelity(args.base_url.rstrip("/"), args.model, args.timeout))

    json_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    md_out.write_text(summarize(results), encoding="utf-8")
    print(f"JSON {json_out}")
    print(f"MD   {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
