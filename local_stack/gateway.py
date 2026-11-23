"""Loopback-only OpenAI chat gateway with request-lifetime model admission."""
from __future__ import annotations

import getpass
import hmac
import json
import os
import secrets
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .registry import StackError
from .runtime import HTTP, ModelRuntime

MAX_BODY_BYTES = 32 * 1024 * 1024


def private_token(path: Path) -> str:
    """Create a per-install token; protect its file before returning it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise StackError("Refusing a symbolic-link token file.", 503)
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        with os.fdopen(descriptor, "w", encoding="ascii") as output:
            output.write(secrets.token_urlsafe(48) + "\n")
    if os.name == "nt":
        account = os.environ.get("USERDOMAIN", ".") + "\\" + getpass.getuser()
        result = subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r", account + ":(F)", "/grant:r", "SYSTEM:(F)"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise StackError("Cannot secure the local control-token file; refusing to serve controls.", 503)
    else:
        path.chmod(0o600)
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32:
        raise StackError("Existing local token is too short; replace it explicitly before serving.", 503)
    return token


def model_catalog(runtime: ModelRuntime) -> dict:
    models = []
    for model in runtime.all_models():
        models.append({"id": model["id"], "object": "model", "owned_by": "watson-local", "name": model["name"], "context_window": model["context"], "contextWindow": model["context"], "max_output_tokens": model["max_output_tokens"], "maxTokens": model["max_output_tokens"], "input": model["input"], "context_presets": model["context_presets"], "validation": model["validation"], "context_verified": bool(model.get("context_verified") or (runtime.record and runtime.record.get("ready") and runtime.record.get("model") == model["id"] and runtime.record.get("context") == model["context"] and runtime.identify(runtime.record["pid"]) == runtime.record["identity"]))})
    return {"object": "list", "data": models}


def prepare_chat(body: dict, model: dict) -> dict:
    """Apply bounded defaults without accepting inference-time launch overrides."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise StackError("Chat requests require a messages array.")
    allowed = {"model", "messages", "temperature", "top_p", "top_k", "min_p", "max_tokens", "max_completion_tokens", "tools", "tool_choice", "parallel_tool_calls", "stream", "stream_options", "frequency_penalty", "presence_penalty", "response_format", "seed", "logit_bias", "stop", "n", "reasoning_effort", "chat_template_kwargs", "repeat_penalty", "store"}
    if set(body) - allowed:
        raise StackError("Unsupported inference fields; routing, credentials and runtime overrides are not accepted.")
    if body.get("store", False) is not False:
        raise StackError("Request storage is not supported; omit store or use false.")
    for message in body["messages"]:
        if not isinstance(message, dict):
            raise StackError("Each message must be an object.")
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    raise StackError("Message content parts must be objects.")
                if part.get("type") == "image_url":
                    if "image" not in model["input"]:
                        raise StackError("This model profile does not support images.")
                    value = part.get("image_url", {})
                    url = value.get("url") if isinstance(value, dict) else value
                    if not isinstance(url, str) or not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:image/gif;base64,")):
                        raise StackError("Only inline image data URLs are accepted; remote fetches are disabled.")
                elif part.get("type") != "text":
                    raise StackError("Only text and inline image content parts are supported.")
    result = dict(body)
    # pi-ai sends store=false for custom OpenAI-compatible URLs. It is a
    # no-op here; do not pass a storage directive to the downstream proxies.
    result.pop("store", None)
    output_keys = [key for key in ("max_tokens", "max_completion_tokens") if key in result]
    if len(output_keys) > 1:
        raise StackError("Specify max_tokens or max_completion_tokens, not both.")
    key = output_keys[0] if output_keys else "max_tokens"
    requested = result.get(key, model["max_output_tokens"])
    if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= model["max_output_tokens"]:
        raise StackError(f"Output must be between 1 and {model['max_output_tokens']} tokens for this profile.")
    result[key] = requested
    if result.get("n", 1) != 1:
        raise StackError("This single-slot launcher supports n=1 only.")
    if "stream" in result and not isinstance(result["stream"], bool):
        raise StackError("stream must be boolean.")
    if model.get("reasoning_effort"):
        result.setdefault("temperature", 1.0)
        kwargs = result.get("chat_template_kwargs", {})
        if not isinstance(kwargs, dict) or set(kwargs) - {"reasoning_effort"}:
            raise StackError("Only reasoning_effort may be overridden in chat_template_kwargs.")
        if kwargs.get("reasoning_effort", model["reasoning_effort"]) not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise StackError("Unsupported reasoning effort.")
        result["chat_template_kwargs"] = {"reasoning_effort": model["reasoning_effort"], **kwargs}
    return result


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, runtime, *, inference_key=None, control_key=None, upstream="http://127.0.0.1:18083/v1", opener=HTTP):
        if address[0] != "127.0.0.1":
            raise StackError("Gateway must bind IPv4 loopback only.")
        key = inference_key or os.environ.get("LOCAL_STACK_API_KEY")
        if not key or len(key) < 32:
            raise StackError("A dedicated LOCAL_STACK_API_KEY of at least 32 characters is required.", 503)
        parsed = urllib.parse.urlsplit(upstream)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise StackError("Upstream must be an uncredentialed HTTP URL on IPv4 loopback.")
        self.runtime = runtime
        self.inference_key = key
        self.control_key = control_key or private_token(runtime.state_dir / "control.token")
        self.upstream = upstream.rstrip("/")
        self.opener = opener
        self.direct = parsed.port == runtime.backend_port
        super().__init__(address, GatewayHandler)

    def upstream_headers(self):
        key = os.environ.get("LLAMA_CPP_API_KEY") if self.direct else os.environ.get("LITELLM_MASTER_KEY")
        if not key:
            raise StackError("The downstream authentication environment is not configured.", 503)
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        if not self.direct:
            mapping = {"HEADROOM_PROXY_TOKEN": "x-headroom-proxy-token", "HEADROOM_PROJECT_ID": "x-headroom-project-id", "HEADROOM_USER_ID": "x-headroom-user-id"}
            for variable, header in mapping.items():
                if os.environ.get(variable):
                    headers[header] = os.environ[variable]
        return headers


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WatsonLocal/0.1"

    def log_message(self, format, *args):
        # No request bodies, authorization headers, URL queries or command lines in logs.
        return

    def _json(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _validate_request(self, control=False, public=False):
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host", "").lower() not in expected:
            raise StackError("Invalid local Host header.", 403)
        if self.headers.get("Origin") is not None:
            raise StackError("Browser-origin requests are not accepted by this terminal launcher.", 403)
        if urllib.parse.urlsplit(self.path).query:
            raise StackError("Query strings are not accepted.")
        if public:
            return
        key = self.server.control_key if control else self.server.inference_key
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, "Bearer " + key):
            raise StackError("A valid local authorization token is required.", 401)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise StackError("Chunked request bodies are not supported.", 411)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise StackError("Invalid Content-Length.", 411)
        if not 0 < length <= MAX_BODY_BYTES:
            raise StackError("Request body is missing or exceeds the 32 MiB limit.", 413)
        if self.headers.get_content_type() != "application/json":
            raise StackError("Content-Type must be application/json.", 415)
        self.connection.settimeout(30)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise StackError("Incomplete request body.")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise StackError("Request is not valid UTF-8 JSON.") from exc
        if not isinstance(body, dict):
            raise StackError("Request body must be an object.")
        return body

    def _error(self, exc):
        self.close_connection = True
        if isinstance(exc, StackError):
            self._json(exc.status, {"error": {"message": str(exc), "type": "local_stack_error"}})
        else:
            self._json(503, {"error": {"message": "Local stack operation failed; inspect its local status and logs.", "type": "local_stack_error"}})

    def do_OPTIONS(self):
        self._json(403, {"error": {"message": "Browser access is disabled."}})

    def do_GET(self):
        try:
            if self.path == "/health":
                self._validate_request(public=True)
                self._json(200, {"status": "ok", "gateway": "watson-local", "model_ready": bool(self.server.runtime.record)})
            elif self.path == "/v1/models":
                self._validate_request()
                self._json(200, model_catalog(self.server.runtime))
            elif self.path == "/control/status":
                self._validate_request(control=True)
                self._json(200, self.server.runtime.status())
            else:
                raise StackError("Unknown route.", 404)
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        headers_sent = False
        admission = False
        try:
            control = self.path.startswith("/control/")
            self._validate_request(control=control)
            body = self._body()
            runtime = self.server.runtime
            if self.path == "/control/load":
                self._json(200, runtime.load(body.get("model"), body.get("context")))
            elif self.path == "/control/configure":
                self._json(200, runtime.configure(body.get("model"), body.get("context")))
            elif self.path == "/control/register":
                self._json(200, runtime.register(body.get("model")))
            elif self.path == "/control/stop":
                self._json(200, runtime.stop())
            elif self.path == "/v1/chat/completions":
                model = runtime.find_model(body.get("model"))
                prepared = prepare_chat(body, model)
                runtime.acquire("inference")
                admission = True
                runtime.ensure_loaded(model["id"])
                request = urllib.request.Request(self.server.upstream + "/chat/completions", data=json.dumps(prepared).encode("utf-8"), headers=self.server.upstream_headers(), method="POST")
                try:
                    upstream = self.server.opener.open(request, timeout=600)
                except urllib.error.HTTPError as exc:
                    # Upstream error bodies can contain prompts, credentials or internal paths.
                    raise StackError(f"The model/proxy rejected the request (HTTP {exc.code}); inspect local service logs.", 502) from exc
                with upstream:
                    self.send_response(upstream.status)
                    self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    headers_sent = True
                    self.close_connection = True
                    read = getattr(upstream, "read1", upstream.read)
                    while True:
                        chunk = read(16384)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
            elif self.path == "/v1/responses":
                raise StackError("This gateway supports Chat Completions. Responses transport is not yet validated through Headroom.", 501)
            else:
                raise StackError("Unknown route.", 404)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            if headers_sent:
                self.close_connection = True
            else:
                self._error(exc)
        finally:
            if admission:
                # This executes only after upstream is closed, including client cancellation.
                self.server.runtime.release()
