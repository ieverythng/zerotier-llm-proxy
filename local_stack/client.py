"""Authenticated terminal client; credentials never travel in URLs or logs."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .registry import StackError
from .runtime import NoRedirect


class StackClient:
    def __init__(self, base_url="http://127.0.0.1:4141", *, state_dir=None, timeout=900, opener=None):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise StackError("Terminal control URL must use IPv4 loopback with no credentials or path.")
        self.base_url = base_url.rstrip("/")
        self.state_dir = Path(state_dir or Path(__file__).resolve().parents[1] / ".runtime" / "local-stack")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _token(self, control):
        if not control and os.environ.get("LOCAL_STACK_API_KEY"):
            return os.environ["LOCAL_STACK_API_KEY"]
        name = "control.token" if control else "inference.token"
        try:
            return (self.state_dir / name).read_text(encoding="ascii").strip()
        except OSError as exc:
            raise StackError("Local launcher credentials are unavailable. Run scripts/windows/Start-LocalModelStack.ps1 first.", 503) from exc

    def _request(self, path, body=None):
        control = path.startswith("/control/")
        headers = {"Authorization": "Bearer " + self._token(control)}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method="GET" if body is None else "POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                payload = json.load(exc)
                message = payload.get("error", {}).get("message", "Local stack rejected the operation.")
            except (ValueError, OSError):
                message = "Local stack rejected the operation."
            raise StackError(message, exc.code) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise StackError("Local gateway is unavailable. Run scripts/windows/Start-LocalModelStack.ps1 first.", 503) from exc

    def status(self):
        return self._request("/control/status")

    def models(self):
        return self._request("/v1/models").get("data", [])

    def load(self, model, context=None):
        body = {"model": model}
        if context is not None:
            body["context"] = context
        return self._request("/control/load", body)

    def configure(self, model, context):
        return self._request("/control/configure", {"model": model, "context": context})

    def register(self, modeldict):
        return self._request("/control/register", {"model": modeldict})

    def stop(self):
        return self._request("/control/stop", {})
