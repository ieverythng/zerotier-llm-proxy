import argparse
import http.client
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DFlashProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_base = "http://127.0.0.1:8080"
    max_output_tokens_cap = int(os.environ.get("DFLASH_PROXY_MAX_OUTPUT_TOKENS", "1024"))
    model_aliases = {
        "qwen36-turbo-hermes": "qwen36-turbo-hermes-spec",
        "qwen36-turbo-hermes-llama": "qwen36-turbo-hermes-spec",
    }

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
        if self.path == "/v1/models":
            return self._models()
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
        if self.command == "POST" and self.path == "/v1/completions":
            return self._completion_via_responses(body)
        if self.command == "POST" and self.path == "/v1/responses":
            return self._responses(body)

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
            payload = self._error_payload("DFlash proxy upstream failure", exc)
            self._send(502, json.dumps(payload).encode("utf-8"))

    def _responses(self, body):
        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
            upstream = self._responses_request(payload)
            self._send(200, json.dumps(upstream).encode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:
            payload = self._error_payload("DFlash responses forwarding failure", exc)
            self._send(502, json.dumps(payload).encode("utf-8"))

    def _models(self):
        try:
            upstream_url = self.upstream_base.rstrip("/") + "/v1/models"
            request = urllib.request.Request(
                upstream_url,
                headers={"Accept": "application/json", "Connection": "close"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    self._send(response.status, response.read(), response.headers.get("Content-Type", "application/json"))
            except (ConnectionAbortedError, ConnectionResetError, urllib.error.URLError):
                proc = subprocess.run(
                    ["curl.exe", "-sS", "--max-time", "30", "-H", "Accept: application/json", upstream_url],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
                self._send(200, proc.stdout)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:
            payload = self._error_payload("DFlash models forwarding failure", exc)
            self._send(502, json.dumps(payload).encode("utf-8"))

    def _error_payload(self, message, exc):
        details = repr(exc)
        if not details or details == "Exception()":
            details = exc.__class__.__name__
        return {
            "error": {
                "message": f"{message}: {details}",
                "type": "proxy_error",
            }
        }

    def _responses_request(self, payload):
        self._cap_output_tokens(payload)
        upstream = urlparse(self.upstream_base.rstrip("/") + "/v1/responses")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        conn_cls = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(upstream.hostname, upstream.port, timeout=900)
        path = upstream.path or "/v1/responses"
        if upstream.query:
            path += "?" + upstream.query

        try:
            conn.request(
                "POST",
                path,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Connection": "close",
                },
            )
            response = conn.getresponse()
            response_body = response.read()
            if response.status >= 400:
                raise urllib.error.HTTPError(
                    self.upstream_base.rstrip("/") + "/v1/responses",
                    response.status,
                    response.reason,
                    response.headers,
                    None,
                )
            return json.loads(response_body.decode("utf-8"))
        except (ConnectionAbortedError, ConnectionResetError, http.client.HTTPException):
            return self._responses_request_with_curl(body)
        finally:
            conn.close()

    def _responses_request_with_curl(self, body):
        upstream_url = self.upstream_base.rstrip("/") + "/v1/responses"
        proc = subprocess.run(
            [
                "curl.exe",
                "-sS",
                "--max-time",
                "900",
                "-X",
                "POST",
                "-H",
                "Accept: application/json",
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                "@-",
                upstream_url,
            ],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
        return json.loads(proc.stdout.decode("utf-8"))

    def _cap_output_tokens(self, payload):
        if self.max_output_tokens_cap <= 0:
            return
        for key in ("max_output_tokens", "max_tokens", "max_completion_tokens"):
            value = payload.get(key)
            if isinstance(value, int) and value > self.max_output_tokens_cap:
                payload[key] = self.max_output_tokens_cap

    def _response_text(self, upstream):
        text = ""
        for output in upstream.get("output", []):
            for item in output.get("content", []):
                if item.get("type") in ("output_text", "text"):
                    text += item.get("text", "")
        return text

    def _response_usage(self, upstream):
        usage = upstream.get("usage") or {}
        return {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

    def _model_name(self, requested):
        model = requested or "qwen36-turbo-hermes-spec"
        return self.model_aliases.get(model, model)

    def _chat_via_responses(self, body):
        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
            messages = payload.get("messages") or []
            response_input = []
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
                response_input.append({"role": role, "content": content})

            response_payload = {
                "model": self._model_name(payload.get("model")),
                "input": response_input,
                "max_output_tokens": payload.get("max_tokens", payload.get("max_completion_tokens", 512)),
            }
            for key in ("temperature", "top_p", "top_k", "seed", "frequency_penalty"):
                if key in payload:
                    response_payload[key] = payload[key]

            upstream = self._responses_request(response_payload)
            text = self._response_text(upstream)
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
                "usage": self._response_usage(upstream),
            }
            self._send(200, json.dumps(chat).encode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:
            payload = self._error_payload("DFlash chat->responses translation failure", exc)
            self._send(502, json.dumps(payload).encode("utf-8"))

    def _completion_via_responses(self, body):
        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
            prompt = payload.get("prompt", "")
            if isinstance(prompt, list):
                prompt = "\n".join(str(item) for item in prompt)

            response_payload = {
                "model": self._model_name(payload.get("model")),
                "input": str(prompt),
                "max_output_tokens": payload.get("max_tokens", payload.get("max_completion_tokens", 512)),
            }
            for key in ("temperature", "top_p", "top_k", "seed", "frequency_penalty", "presence_penalty"):
                if key in payload:
                    response_payload[key] = payload[key]

            upstream = self._responses_request(response_payload)
            text = self._response_text(upstream)
            completion = {
                "id": upstream.get("id", f"cmpl-{uuid.uuid4().hex}"),
                "object": "text_completion",
                "created": 1700000000,
                "model": upstream.get("model", response_payload["model"]),
                "choices": [{
                    "text": text,
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": "stop",
                }],
                "usage": self._response_usage(upstream),
            }
            self._send(200, json.dumps(completion).encode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json"))
        except Exception as exc:
            payload = self._error_payload("DFlash completions->responses translation failure", exc)
            self._send(502, json.dumps(payload).encode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--upstream", default="http://127.0.0.1:8080")
    parser.add_argument("--max-output-tokens", type=int, default=DFlashProxyHandler.max_output_tokens_cap)
    args = parser.parse_args()

    DFlashProxyHandler.upstream_base = args.upstream
    DFlashProxyHandler.max_output_tokens_cap = args.max_output_tokens
    server = ThreadingHTTPServer((args.host, args.port), DFlashProxyHandler)
    print(
        f"DFlash proxy listening on http://{args.host}:{args.port} -> {args.upstream} "
        f"(max_output_tokens_cap={args.max_output_tokens})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
