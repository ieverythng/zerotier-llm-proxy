"""Small OpenAI-contract probe usable from Windows or the NAO Linux host.

No tools are executed. The function-call case only validates returned JSON.
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path


def verify(base_url, model, timeout):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("OPENAI_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
    rows = []

    def request(name, payload, validator, stream=False):
        started = time.monotonic()
        body = {"model": model, "temperature": 0, "max_tokens": 96, **payload}
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            json.dumps(body).encode(), headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if stream:
                    pieces, done = [], False
                    for raw_line in response:
                        if time.monotonic() - started > timeout:
                            raise TimeoutError("stream exceeded probe deadline")
                        line = raw_line.decode().strip()
                        if line == "data: [DONE]":
                            done = True
                            break
                        if line.startswith("data: "):
                            event = json.loads(line[6:])
                            for choice in event.get("choices", []):
                                pieces.append(choice.get("delta", {}).get("content") or "")
                    result = {"content": "".join(pieces), "done": done}
                else:
                    result = json.load(response)
                    if result["usage"]["completion_tokens"] > body["max_tokens"]:
                        raise AssertionError("completion exceeded requested token cap")
            if not validator(result):
                raise AssertionError("unexpected response: " + json.dumps(result, ensure_ascii=False)[:700])
            row = {"case": name, "passed": True, "elapsed_s": round(time.monotonic() - started, 3)}
        except Exception as exc:
            row = {"case": name, "passed": False, "error": str(exc), "elapsed_s": round(time.monotonic() - started, 3)}
        rows.append(row)

    marker = "WATSON_READY_71429"
    messages = [{"role": "user", "content": f"Reply with exactly {marker} and nothing else."}]
    for i in range(2):
        request(f"marker_{i + 1}", {"messages": messages, "max_tokens": 24},
                lambda r: (r["choices"][0]["message"].get("content") or "").strip() == marker)
    request("stream", {"messages": messages, "stream": True, "max_tokens": 24},
            lambda r: r["done"] and r["content"].strip() == marker, stream=True)
    request("hermes_reasoning_setting", {"messages": messages, "reasoning_effort": "medium", "max_tokens": 24},
            lambda r: (r["choices"][0]["message"].get("content") or "").strip() == marker)
    tool = {"type": "function", "function": {
        "name": "record_probe", "description": "Record a probe value.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False},
    }}

    def valid_tool(r):
        calls = r["choices"][0]["message"].get("tool_calls", [])
        return (len(calls) == 1 and calls[0]["function"]["name"] == "record_probe"
                and json.loads(calls[0]["function"]["arguments"]) == {"value": marker})

    request("tool_call", {
        "messages": [{"role": "user", "content": f"Call record_probe with value {marker}."}],
        "tools": [tool], "tool_choice": {"type": "function", "function": {"name": "record_probe"}},
    }, valid_tool)
    request("strict_json", {
        "messages": [{"role": "user", "content": 'Return JSON only with key "answer" equal to 17 + 25.'}],
        "response_format": {"type": "json_object"},
    }, lambda r: json.loads(r["choices"][0]["message"]["content"]) == {"answer": 42})
    return {"base_url": base_url, "model": model, "passed": all(r["passed"] for r in rows), "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="qwen3.8")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.base_url, args.model, args.timeout)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["passed"] else 1)
