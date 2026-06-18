import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DFlashProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_base = "http://127.0.0.1:8080"

    def log_message(self, fmt, *args):
        sys.stderr.write("[dflash-proxy] " + (fmt % args) + "\n")

    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self._send(200, b'{"status":"ok"}\n')
            return
        self._forward(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self._forward(body)

    def _forward(self, body):
        # Current Windows dflash_server build serves /v1/responses reliably, while
        # /v1/chat/completions aborts POST connections. Translate OpenAI chat
        # requests into Responses API calls and wrap the result back into a chat
        # completion for LiteLLM/Hermes compatibility.
        if self.command == "POST" and self.path == "/v1/chat/completions":
            return self._chat_via_responses(body)

        upstream_url = self.upstream_base.rstrip("/") + self.path
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = self.headers.get("Content-Type", "application/json")
        if self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]

        request = urllib.request.Request(
            upstream_url,
            data=body,
            headers=headers,
            method=self.command,
        )

        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                response_body = response.read()
                content_type = response.headers.get("Content-Type", "application/json")
                self._send(response.status, response_body, content_type)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:
            payload = {
                "error": {
                    "message": f"DFlash proxy upstream failure: {exc}",
                    "type": "proxy_error",
                }
            }
            self._send(502, json.dumps(payload).encode("utf-8"))

    def _chat_via_responses(self, body):
        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
            messages = payload.get("messages") or []
            prompt_parts = []
            for message in messages:
                role = message.get("role", "user")
                content = message.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") in ("text", "input_text"):
                                text_parts.append(item.get("text", ""))
                        else:
                            text_parts.append(str(item))
                    content = "\n".join(p for p in text_parts if p)
                prompt_parts.append(f"{role}: {content}")
            prompt_parts.append("assistant:")

            response_payload = {
                "model": payload.get("model", "qwen36-turbo-hermes-spec"),
                "input": "\n".join(prompt_parts),
                "max_output_tokens": payload.get("max_tokens", payload.get("max_completion_tokens", 512)),
            }
            for key in ("temperature", "top_p", "top_k", "seed", "frequency_penalty"):
                if key in payload:
                    response_payload[key] = payload[key]

            upstream_url = self.upstream_base.rstrip("/") + "/v1/responses"
            request = urllib.request.Request(
                upstream_url,
                data=json.dumps(response_payload).encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=900) as response:
                raw = response.read()
            upstream = json.loads(raw.decode("utf-8"))

            text = ""
            for output in upstream.get("output", []):
                for item in output.get("content", []):
                    if item.get("type") in ("output_text", "text"):
                        text += item.get("text", "")

            usage = upstream.get("usage") or {}
            chat = {
                "id": upstream.get("id", "chatcmpl-dflash"),
                "object": "chat.completion",
                "created": 1700000000,
                "model": upstream.get("model", response_payload["model"]),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }
            self._send(200, json.dumps(chat).encode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:
            payload = {
                "error": {
                    "message": f"DFlash chat->responses translation failure: {exc}",
                    "type": "proxy_error",
                }
            }
            self._send(502, json.dumps(payload).encode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--upstream", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    DFlashProxyHandler.upstream_base = args.upstream
    server = ThreadingHTTPServer((args.host, args.port), DFlashProxyHandler)
    print(f"DFlash proxy listening on http://{args.host}:{args.port} -> {args.upstream}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
