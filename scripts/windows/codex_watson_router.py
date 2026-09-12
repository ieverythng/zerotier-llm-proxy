"""Loopback-only model router for a Watson-enabled Codex desktop profile."""

from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import httpx


HOP_BY_HOP = {
    "connection", "content-length", "host", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade",
}
LOCAL_MODELS = {"qwen3.8", "qwen3.8:latest"}


def decode_request(body: bytes, encoding: str) -> bytes:
    if encoding.lower().strip() != "zstd":
        return body
    executable = shutil.which("zstd")
    if not executable:
        raise RuntimeError("zstd-compressed request received but zstd.exe is unavailable")
    result = subprocess.run(
        [executable, "-d", "--stdout", "--quiet"],
        input=body,
        capture_output=True,
        check=True,
    )
    return result.stdout


def extract_model(body: bytes) -> str:
    if not body:
        return ""
    value = json.loads(body).get("model", "")
    return value.strip() if isinstance(value, str) else ""


class Router(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WatsonCodexRouter/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/_health":
            payload = b'{"ok":true,"service":"watson-codex-router"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
            return
        if self.headers.get("Upgrade", "").lower() == "websocket":
            payload = b'{"error":"Watson router uses HTTP Responses transport"}'
            self.send_response(426)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
            return
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def _proxy(self) -> None:
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                self.send_error(403, "Watson Codex router accepts loopback requests only")
                return
        except ValueError:
            self.send_error(403, "Invalid client address")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        try:
            body = decode_request(raw_body, self.headers.get("Content-Encoding", ""))
            model = extract_model(body)
        except (json.JSONDecodeError, RuntimeError, subprocess.CalledProcessError) as error:
            self.send_error(400, str(error))
            return

        local = model.lower() in LOCAL_MODELS
        self._last_local = local
        if local:
            target = urljoin(self.server.local_base.rstrip("/") + "/", self.path.lstrip("/"))
        else:
            suffix = self.path[3:] if self.path.startswith("/v1/") else self.path
            target = urljoin(self.server.chatgpt_base.rstrip("/") + "/", suffix.lstrip("/"))

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != "content-encoding"
        }
        if local:
            for key in list(headers):
                if key.lower() in {"authorization", "chatgpt-account-id", "cookie"}:
                    del headers[key]

        try:
            with self.server.client.stream(
                self.command, target, headers=headers, content=body
            ) as response:
                self.send_response(response.status_code)
                for key, value in response.headers.multi_items():
                    if key.lower() not in HOP_BY_HOP:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk in response.iter_raw():
                    if chunk:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                self.close_connection = True
        except (httpx.HTTPError, BrokenPipeError, ConnectionResetError) as error:
            if not self.wfile.closed:
                self.send_error(502, str(error))

    def log_message(self, format: str, *args: object) -> None:
        route = "local" if getattr(self, "_last_local", False) else "native"
        super().log_message(f"route={route} {format}", *args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4010)
    parser.add_argument("--local-base", default="http://127.0.0.1:4000")
    parser.add_argument("--chatgpt-base", default="https://chatgpt.com/backend-api/codex")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Router)
    server.local_base = args.local_base
    server.chatgpt_base = args.chatgpt_base
    server.client = httpx.Client(timeout=None, follow_redirects=False)
    try:
        server.serve_forever()
    finally:
        server.client.close()


if __name__ == "__main__":
    main()
