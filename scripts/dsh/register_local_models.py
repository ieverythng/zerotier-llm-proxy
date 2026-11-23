"""Register only Watson Local through DSH 0.2.2-rc.5's authenticated Settings API.

No YAML edits, plugin installation, default-model writes, or inference requests.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

PROVIDER = "watson-local"
CREDENTIAL_REF = "LOCAL_STACK_API_KEY"
MODEL_IDS = ("saluki27b", "inkling2-mini")
ROOT = Path(__file__).resolve().parents[2]


class RegistrationError(RuntimeError):
    """A public diagnostic that must never include credential material."""


def loopback_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.fragment):
        raise RegistrationError("Only literal loopback HTTP URLs without userinfo/fragments are supported.")
    try:
        parsed.port
    except ValueError:
        raise RegistrationError("Invalid loopback port.") from None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))


def positive_int(value, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise RegistrationError(f"{field} must be a positive integer.")
    return value


def build_provider(registry: dict, base_url: str, advertised: dict | None = None) -> dict:
    """Pure catalog construction. Undeclared or unverified capacity is never guessed."""
    base_url = loopback_url(base_url)
    if urllib.parse.urlsplit(base_url).query:
        raise RegistrationError("Gateway base URL must not contain a query.")
    if not isinstance(registry, dict):
        raise RegistrationError("Registry must be a JSON object.")
    schema = registry.get("schema_version", registry.get("schema"))
    if type(schema) is not int or schema != 1:
        raise RegistrationError("Expected registry schema version 1.")
    entries = registry.get("models")
    if not isinstance(entries, list):
        raise RegistrationError("Registry models must be a list.")
    indexed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise RegistrationError("Invalid registry model entry.")
        if entry["id"] in indexed:
            raise RegistrationError("Duplicate registry model id.")
        indexed[entry["id"]] = entry
    discovered = {}
    if advertised is not None:
        if not isinstance(advertised, dict) or not isinstance(advertised.get("data"), list):
            raise RegistrationError("Gateway model catalog must contain a data list.")
        for entry in advertised["data"]:
            if isinstance(entry, dict) and entry.get("id") in MODEL_IDS:
                if entry["id"] in discovered:
                    raise RegistrationError("Duplicate gateway model id.")
                discovered[entry["id"]] = entry
    models = []
    for model_id in MODEL_IDS:
        if model_id not in indexed:
            raise RegistrationError(f"Registry is missing {model_id}.")
        entry = indexed[model_id]
        live = discovered.get(model_id, {})
        context = positive_int(live.get("context_window", entry.get("context")), "context")
        output = positive_int(live.get("max_output_tokens", entry.get("max_output_tokens")), "max_output_tokens")
        if output >= context:
            raise RegistrationError("Output budget must leave room for input within context.")
        # Only explicit attestation marks a catalog capacity verified. A listing alone is not a probe.
        verified = live.get("context_verified") is True or (not live and entry.get("context_verified") is True)
        state = "verified" if verified else "provisional"
        name = entry.get("name", model_id)
        if not isinstance(name, str) or not name.strip() or any(ord(c) < 32 for c in name):
            raise RegistrationError("Invalid model display name.")
        modalities = ["text"]
        declared_input = entry.get("input", ["text"])
        if not isinstance(declared_input, list) or any(item not in {"text", "image"} for item in declared_input):
            raise RegistrationError("Model input modalities must be a text/image list.")
        if model_id == "saluki27b" and "image" in declared_input:
            projector = entry.get("mmproj")
            if isinstance(projector, str) and Path(projector).is_file():
                modalities.append("image")
        models.append({
            "id": model_id,
            "name": f"{name} · {context:,} ctx ({state})",
            "contextWindow": context,
            "maxTokens": output,
            "input": modalities,
            "reasoningEfforts": False,
        })
    return {"displayName": "Watson Local", "api": "openai-completions", "baseURL": base_url,
            "apiKeyEnv": CREDENTIAL_REF, "models": models}


def provider_ops(namespace: dict, desired: dict) -> list[dict]:
    """Touch only owned leaf fields; preserve hosted routes and unknown local fields."""
    value = namespace.get("value", {})
    if not isinstance(value, dict) or not isinstance(value.get("providers", {}), dict):
        raise RegistrationError("Invalid llm-pi-ai settings descriptor.")
    current = value.get("providers", {}).get(PROVIDER, {})
    if not isinstance(current, dict):
        raise RegistrationError("Existing local provider is not an object.")
    return [{"op": "set", "path": ["providers", PROVIDER, key], "value": copy.deepcopy(value)}
            for key, value in desired.items() if current.get(key) != value]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RegistrationError("Unexpected HTTP redirect; refusing credential forwarding.")


class DshRpc:
    def __init__(self, url: str, cookie: str | None = None, opener=None):
        parsed = urllib.parse.urlsplit(loopback_url(url))
        if parsed.path not in {"", "/"} or parsed.query:
            raise RegistrationError("DSH URL must be its clean loopback origin.")
        self.origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        self.cookie = cookie
        self.cookies = http.cookiejar.CookieJar()
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())

    def exchange_launch_url(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(loopback_url(url))
        if parsed.path not in {"", "/"} or urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")) != self.origin:
            raise RegistrationError("Launch URL must name this exact DSH origin and root path.")
        query = urllib.parse.parse_qs(parsed.query)
        if set(query) != {"token"} or len(query["token"]) != 1 or not query["token"][0]:
            raise RegistrationError("Launch URL must contain exactly one launch token.")
        # Token exchange is the documented root GET; deliberately never follow its redirect.
        request = urllib.request.Request(url, headers={"Origin": self.origin})
        try:
            self.opener.open(request, timeout=15).close()
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303):
                raise RegistrationError(f"DSH token exchange failed (HTTP {error.code}).") from None
        except RegistrationError:
            # HTTPCookieProcessor has already processed the authorized 302's Set-Cookie.
            pass
        except OSError:
            raise RegistrationError("DSH token exchange could not reach the Host.") from None
        if not list(self.cookies):
            raise RegistrationError("DSH did not issue an authentication cookie; use the native Models page.")

    def call(self, endpoint: str, args: dict):
        rpc_id = str(uuid.uuid4())
        body = {"type": "client-request", "rpcId": rpc_id, "method": endpoint, "payload": {"args": args}}
        headers = {"Content-Type": "application/json", "Origin": self.origin}
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(self.origin + "/api/" + endpoint,
                                         data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=20) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as error:
            raise RegistrationError(f"DSH RPC refused (HTTP {error.code}); authenticate through the native launch URL or Models page.") from None
        except (OSError, ValueError):
            raise RegistrationError("DSH RPC transport or JSON response failed.") from None
        if reply.get("rpcId") != rpc_id or reply.get("type") != "server-response":
            raise RegistrationError("Unexpected DSH RPC response envelope.")
        result = reply.get("result", {})
        if result.get("ok") is not True:
            code = result.get("error", {}).get("code", "unknown")
            safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_/-]+", code) else "unknown"
            raise RegistrationError(f"DSH rejected {endpoint} ({safe_code}); no fallback file edits were attempted.")
        return result.get("value")


def apply_registration(rpc, desired: dict, inference_key: str, snapshot: Path | None = None) -> dict:
    if not inference_key or any(ord(c) <= 32 or ord(c) > 126 for c in inference_key):
        raise RegistrationError("Inference token must be nonempty printable ASCII without whitespace.")
    document = rpc.call("settings/describe", {})
    if document.get("writable") is not True:
        raise RegistrationError("DSH settings are not writable.")
    matches = [entry for entry in document.get("namespaces", []) if entry.get("ns") == "llm-pi-ai"]
    if len(matches) != 1:
        raise RegistrationError("Exactly one live llm-pi-ai settings namespace is required.")
    namespace = matches[0]
    revision = namespace.get("revision")
    if type(revision) is not int:
        raise RegistrationError("DSH settings did not provide a concurrency revision.")
    ops = provider_ops(namespace, desired)
    # Snapshot contains only our managed public fields, never foreign providers, headers or credentials.
    if snapshot is not None:
        previous = namespace.get("value", {}).get("providers", {}).get(PROVIDER, {})
        safe = {key: previous[key] for key in desired if key in previous}
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with snapshot.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"provider": PROVIDER, "revision": revision, "managed_fields": safe}, indent=2) + "\n")
    if ops:
        rpc.call("settings/mutate", {"ns": "llm-pi-ai", "ops": ops, "expectedRevision": revision})
    try:
        rpc.call("credentials/set", {"ref": CREDENTIAL_REF, "value": inference_key})
    except RegistrationError:
        raise RegistrationError("Local provider settings may be saved, but credential storage failed. Rerun authenticated apply; no hosted route/default was changed.") from None
    after = rpc.call("settings/describe", {})
    namespaces = [entry for entry in after.get("namespaces", []) if entry.get("ns") == "llm-pi-ai"]
    if len(namespaces) != 1 or provider_ops(namespaces[0], desired):
        raise RegistrationError("Post-save local provider verification failed.")
    info = rpc.call("credentials/describe", {"refs": [CREDENTIAL_REF]})
    if info.get(CREDENTIAL_REF, {}).get("configured") is not True:
        raise RegistrationError("Local provider credential is not configured after save.")
    return {"provider": PROVIDER, "settings_changed": bool(ops), "credential_configured": True,
            "models": [model["id"] for model in desired["models"]], "default_changed": False}


def gateway_models(base_url: str, key: str) -> dict:
    request = urllib.request.Request(loopback_url(base_url) + "/models", headers={"Authorization": "Bearer " + key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise RegistrationError("Gateway model metadata unavailable; use registry-only planning or start the gateway.") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "config/models.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:4141/v1")
    parser.add_argument("--dsh-url", default="http://127.0.0.1:64938")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default: show only public registration plan")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--use-gateway-metadata", action="store_true")
    parser.add_argument("--token-file", type=Path, default=ROOT / ".runtime/local-stack/inference.token")
    parser.add_argument("--cookie-env", default="DSH_AUTH_COOKIE")
    parser.add_argument("--launch-url-env", default="DSH_LAUNCH_URL")
    parser.add_argument("--snapshot", type=Path, help="Optional public managed-field snapshot; never contains secret values")
    args = parser.parse_args(argv)
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8-sig"))
        key = ""
        if args.apply or args.use_gateway_metadata:
            key = os.environ.get(CREDENTIAL_REF, "")
            if not key and args.token_file.is_file():
                key = args.token_file.read_text(encoding="utf-8").strip()
            if not key:
                raise RegistrationError("LOCAL_STACK_API_KEY or the inference token file is required; control tokens are never accepted.")
        advertised = gateway_models(args.base_url, key) if args.use_gateway_metadata else None
        desired = build_provider(registry, args.base_url, advertised)
        if not args.apply:
            print(json.dumps({"mode": "dry-run", "provider": PROVIDER, "config": desired,
                              "default_changed": False, "credential_reference": CREDENTIAL_REF}, indent=2))
            return 0
        rpc = DshRpc(args.dsh_url, os.environ.get(args.cookie_env))
        if not rpc.cookie:
            launch = os.environ.get(args.launch_url_env)
            if not launch:
                raise RegistrationError("Authentication required: supply DSH_LAUNCH_URL or DSH_AUTH_COOKIE via environment, or use Settings > Models > Custom model API. Clean GUI URL is not an authentication token.")
            rpc.exchange_launch_url(launch)
        snapshot = args.snapshot or ROOT / ".runtime/local-stack" / (
            "dsh-registration-before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        result = apply_registration(rpc, desired, key, snapshot)
        print(json.dumps(result, indent=2))
        return 0
    except (RegistrationError, OSError, ValueError) as error:
        # File/JSON errors may contain paths, not file contents; avoid printing arbitrary provider diagnostics.
        message = str(error) if isinstance(error, RegistrationError) else "Could not read the registry/token or decode configuration JSON."
        print("Registration failed: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
