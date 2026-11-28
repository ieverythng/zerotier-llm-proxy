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
NATIVE_CREDENTIAL_REF = "WATSON_LOCAL_API_KEY"
MODEL_IDS = ("saluki27b", "inkling2-mini")
CAPACITY_ALIAS = "inkling2-mini-long"
CAPACITY_CONTEXT = 150016
CAPACITY_OUTPUT = 10000
DEFAULT_CAPACITY_OUTPUT = 4096
SHORT_ALIAS = "inkling2-mini"
FIXED_VISION_CONTEXTS = {CAPACITY_ALIAS: CAPACITY_CONTEXT, "inkling2-mini-100k": 100096}
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
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            model_id = entry["id"]
            if model_id in MODEL_IDS or model_id in FIXED_VISION_CONTEXTS:
                if model_id in discovered:
                    raise RegistrationError("Duplicate gateway model id.")
                discovered[model_id] = entry
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
    for model_id, context in FIXED_VISION_CONTEXTS.items():
        if model_id not in indexed:
            continue  # Legacy registries do not declare the fixed profiles.
        if advertised is not None and model_id not in discovered:
            raise RegistrationError(f"Gateway is missing fixed vision profile {model_id}.")
        models.append(fixed_vision_model(indexed[model_id], discovered.get(model_id), context))
    return {"displayName": "Watson Local", "api": "openai-completions", "baseURL": base_url,
            "apiKeyEnv": CREDENTIAL_REF, "models": models}


def local_provider(namespace: dict) -> dict:
    value = namespace.get("value", {})
    if not isinstance(value, dict) or not isinstance(value.get("providers", {}), dict):
        raise RegistrationError("Invalid llm-pi-ai settings descriptor.")
    current = value.get("providers", {}).get(PROVIDER, {})
    if not isinstance(current, dict):
        raise RegistrationError("Existing local provider is not an object.")
    return current


def provider_ops(namespace: dict, desired: dict) -> list[dict]:
    """Touch only owned leaf fields; preserve hosted routes and unknown local fields."""
    current = local_provider(namespace)
    wanted = dict(desired)
    if "models" in wanted:
        before = current.get("models", [])
        if not isinstance(before, list):
            raise RegistrationError("Existing local model catalog is not a list.")
        updates = {row["id"]: row for row in wanted["models"]}
        seen = set()
        merged = []
        for row in before:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in seen:
                raise RegistrationError("Existing local model catalog contains an invalid or duplicate ID.")
            seen.add(row["id"])
            merged.append({**row, **updates.pop(row["id"], {})})
        wanted["models"] = merged + list(updates.values())
    return [{"op": "set", "path": ["providers", PROVIDER, key], "value": copy.deepcopy(value)}
            for key, value in wanted.items() if current.get(key) != value]


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
    previous = local_provider(namespace)
    # A native Watson credential may already exist; never write to an arbitrary external reference.
    credential_ref = NATIVE_CREDENTIAL_REF if previous.get("apiKeyEnv") == NATIVE_CREDENTIAL_REF else CREDENTIAL_REF
    desired = {**desired, "apiKeyEnv": credential_ref}
    ops = provider_ops(namespace, desired)
    # Snapshot contains only our managed public fields, never foreign providers, headers or credentials.
    if snapshot is not None:
        safe = {key: previous[key] for key in ("displayName", "api", "baseURL", "apiKeyEnv")
                if key in previous and previous[key] == desired.get(key)}
        if "models" in previous:
            managed_ids = {row["id"] for row in desired["models"]}
            safe["models"] = public_models([row for row in previous["models"] if row["id"] in managed_ids])
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with snapshot.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"provider": PROVIDER, "revision": revision, "managed_fields": safe}, indent=2) + "\n")
    if ops:
        rpc.call("settings/mutate", {"ns": "llm-pi-ai", "ops": ops, "expectedRevision": revision})
    try:
        rpc.call("credentials/set", {"ref": credential_ref, "value": inference_key})
    except RegistrationError:
        raise RegistrationError("Local provider settings may be saved, but credential storage failed. Rerun authenticated apply; no hosted route/default was changed.") from None
    after = rpc.call("settings/describe", {})
    namespaces = [entry for entry in after.get("namespaces", []) if entry.get("ns") == "llm-pi-ai"]
    if len(namespaces) != 1 or provider_ops(namespaces[0], desired):
        raise RegistrationError("Post-save local provider verification failed.")
    info = rpc.call("credentials/describe", {"refs": [credential_ref]})
    if info.get(credential_ref, {}).get("configured") is not True:
        raise RegistrationError("Local provider credential is not configured after save.")
    return {"provider": PROVIDER, "settings_changed": bool(ops), "credential_configured": True,
            "models": [model["id"] for model in desired["models"]], "default_changed": False}


def fixed_vision_model(candidate: dict, live: dict | None, context: int) -> dict:
    """Validate a reviewed registry profile and, when requested, its gateway mirror."""
    for field in ("context", "minimum_context", "maximum_context"):
        if type(candidate.get(field)) is not int or candidate[field] != context:
            raise RegistrationError(f"Inkling candidate must pin minimum, maximum and context to {context}.")
    pairs = [(candidate, "max_output_tokens", CAPACITY_OUTPUT)]
    if live is not None:
        pairs += [(live, "max_output_tokens", CAPACITY_OUTPUT), (live, "context_window", context)]
        pairs += [(live, key, expected) for key, expected in (("contextWindow", context), ("maxTokens", CAPACITY_OUTPUT))
                  if key in live]
    for row, field, expected in pairs:
        if type(row.get(field)) is not int or row[field] != expected:
            raise RegistrationError(f"Registry and live gateway must agree on {context} context / {CAPACITY_OUTPUT} output.")
    for row in (candidate,) if live is None else (candidate, live):
        presets = row.get("context_presets")
        if (presets != [context] or type(presets[0]) is not int
                or row.get("context_verified") is not True):
            raise RegistrationError(f"Each profile record must attest verified fixed [{context}] capacity.")
    modalities = candidate.get("input")
    if modalities != ["text", "image"] or (live is not None and live.get("input") != modalities):
        raise RegistrationError("Registry and live gateway must agree on Inkling input modalities: text plus image.")
    projector = candidate.get("mmproj")
    if not isinstance(projector, str) or Path(projector).suffix.lower() != ".gguf":
        raise RegistrationError("Inkling image input requires a declared GGUF projector.")
    label = "Inkling 2 Mini" if candidate["id"] == CAPACITY_ALIAS else "Inkling 2 Mini fallback"
    return {"id": candidate["id"], "name": f"{label} | {context:,} ctx (fixed verified profile)",
            "contextWindow": context, "maxTokens": CAPACITY_OUTPUT, "input": list(modalities), "reasoningEfforts": False}


def fixed_inkling_candidate(registry: dict, advertised: dict) -> dict:
    """The separate capacity-lock path always requires matching live metadata."""
    def exact(entries, source):
        if not isinstance(entries, list):
            raise RegistrationError(f"{source} model list is missing.")
        matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == CAPACITY_ALIAS]
        if len(matches) != 1:
            raise RegistrationError(f"{source} must contain exactly one fixed Inkling candidate.")
        return matches[0]

    if not isinstance(registry, dict) or not isinstance(advertised, dict):
        raise RegistrationError("Registry and gateway metadata must be objects.")
    return fixed_vision_model(exact(registry.get("models"), "Registry"),
                              exact(advertised.get("data"), "Gateway"), CAPACITY_CONTEXT)


def public_models(models: list) -> list:
    """Rollback evidence is public model metadata, never transport/custom fields."""
    result = []
    for row in models:
        public = {key: row[key] for key in ("id", "name")
                  if isinstance(row.get(key), str) and all(ord(c) >= 32 for c in row[key])}
        public.update({key: row[key] for key in ("contextWindow", "maxTokens")
                       if type(row.get(key)) is int and row[key] > 0})
        if type(row.get("reasoningEfforts")) is bool:
            public["reasoningEfforts"] = row["reasoningEfforts"]
        modalities = row.get("input")
        if isinstance(modalities, list) and all(item in ("text", "image") for item in modalities):
            public["input"] = list(modalities)
        result.append(public)
    return result


def capacity_lock_plan(registry: dict, advertised: dict, document: dict | None,
                       *, base_url="http://127.0.0.1:4141/v1", acknowledge_retire_short=False,
                       output=DEFAULT_CAPACITY_OUTPUT, context=None) -> dict:
    candidate = fixed_inkling_candidate(registry, advertised)
    if context is not None and (type(context) is not int or context != CAPACITY_CONTEXT):
        raise RegistrationError(f"Explicit Inkling context must be {CAPACITY_CONTEXT}.")
    if type(output) is not int or output not in {DEFAULT_CAPACITY_OUTPUT, CAPACITY_OUTPUT}:
        raise RegistrationError(f"Inkling output must be {DEFAULT_CAPACITY_OUTPUT} or {CAPACITY_OUTPUT}.")
    candidate["maxTokens"] = output
    plan = {"mode": "capacity-lock-plan", "provider": PROVIDER, "candidate": candidate,
             "verified_backend_output": CAPACITY_OUTPUT,
            "default_changed": False, "credentials_changed": False, "blockers": [],
            "status": "blocked", "settings_verified": False}
    if document is None:
        plan["blockers"].append("Authenticated live settings/default unavailable; candidate-only proposal, no removal authorized.")
        return plan
    if not isinstance(document, dict) or not isinstance(document.get("namespaces"), list):
        raise RegistrationError("Settings describe response must contain namespaces.")
    namespaces = document["namespaces"]
    adapters = [row for row in namespaces if row.get("ns") == "llm-pi-ai"]
    defaults = [row for row in namespaces if row.get("ns") == "agent-default-model"]
    if len(adapters) != 1 or len(defaults) != 1:
        raise RegistrationError("Exactly one adapter and default-model settings namespace must be inspected.")
    adapter = adapters[0]
    if type(adapter.get("revision")) is not int or type(defaults[0].get("revision")) is not int:
        raise RegistrationError("Both settings namespaces require explicit revisions.")
    provider = adapter.get("value", {}).get("providers", {}).get(PROVIDER)
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), list):
        raise RegistrationError("Capacity lock requires an existing Watson Local model list.")
    if provider.get("api") != "openai-completions" or provider.get("baseURL", "").rstrip("/") != loopback_url(base_url):
        raise RegistrationError("Existing Watson Local endpoint/protocol does not match the inspected gateway.")
    before = provider["models"]
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in before):
        raise RegistrationError("Existing provider contains an invalid model row.")
    ids = [row["id"] for row in before]
    if len(ids) != len(set(ids)):
        raise RegistrationError("Existing model ids are duplicated; repair explicitly before migration.")
    default = defaults[0].get("value", {})
    if not all(isinstance(default.get(key), str) and default[key] for key in ("provider", "model")):
        raise RegistrationError("Actual live deployment default could not be verified.")
    plan["settings_verified"] = True
    if document.get("writable") is not True:
        plan["blockers"].append("Settings are not writable.")
    removing_short = SHORT_ALIAS in ids
    if default["provider"] == PROVIDER and default["model"] == SHORT_ALIAS:
        plan["blockers"].append("Live deployment default still targets inkling2-mini; migrate it natively first. This guard has no override.")
    if removing_short and not acknowledge_retire_short:
        plan["blockers"].append("Retiring short alias requires explicit confirmation that active/saved sessions no longer depend on it.")
    existing_long = CAPACITY_ALIAS in ids
    effective_context = CAPACITY_CONTEXT
    after = []
    for row in before:
        if row["id"] == CAPACITY_ALIAS:
            saved_context = positive_int(row.get("contextWindow", CAPACITY_CONTEXT), "Saved Inkling context")
            effective_context = context if context is not None else min(saved_context, CAPACITY_CONTEXT)
            if effective_context <= output:
                plan["blockers"].append("Saved Inkling context must exceed the requested output budget; adjust it explicitly first.")
                after.append(copy.deepcopy(row))
            else:
                # Only these three fields are owned by the lock on an existing long row.
                after.append({**copy.deepcopy(row), "contextWindow": effective_context, "maxTokens": output,
                              "input": list(candidate["input"])})
        elif row["id"] == SHORT_ALIAS:
            if not existing_long:
                after.append(copy.deepcopy(candidate))
        else:
            after.append(copy.deepcopy(row))
    if not existing_long and not removing_short:
        after.append(copy.deepcopy(candidate))
    if default["provider"] == PROVIDER and default["model"] not in {row["id"] for row in after} and default["model"] != SHORT_ALIAS:
        plan["blockers"].append("Live deployment default is unavailable in the proposed local catalog; repair it natively first.")
    plan.update({"status": "blocked" if plan["blockers"] else "ready",
                 "effective_context_window": effective_context,
                 "settings_changed": before != after, "retiring_short": removing_short,
                 "model_ids_after": [row["id"] for row in after],
                 "_before": copy.deepcopy(before), "_after": after, "_revision": adapter["revision"],
                 "_default": copy.deepcopy(default), "_default_revision": defaults[0]["revision"]})
    return plan


def public_capacity_plan(plan: dict) -> dict:
    return {key: value for key, value in plan.items() if not key.startswith("_")}


def apply_capacity_lock(rpc, registry: dict, advertised: dict, snapshot: Path,
                        *, base_url="http://127.0.0.1:4141/v1", acknowledge_retire_short=False,
                        output=DEFAULT_CAPACITY_OUTPUT, context=None, refresh_advertised=None) -> dict:
    """No credentials/default writes. A stale short default is never overridable."""
    document = rpc.call("settings/describe", {})
    plan = capacity_lock_plan(registry, advertised, document, base_url=base_url,
                              acknowledge_retire_short=acknowledge_retire_short, output=output, context=context)
    if plan["blockers"]:
        raise RegistrationError("Capacity lock refused: " + " ".join(plan["blockers"]))
    # Re-read immediately before mutation. DSH has no cross-namespace transaction;
    # operator must also exclude concurrent selection/default/config writers.
    fresh = capacity_lock_plan(registry, advertised, rpc.call("settings/describe", {}),
                               base_url=base_url, acknowledge_retire_short=acknowledge_retire_short, output=output, context=context)
    if (fresh["blockers"] or fresh["_revision"] != plan["_revision"] or fresh["_before"] != plan["_before"]
            or fresh["_default_revision"] != plan["_default_revision"] or fresh["_default"] != plan["_default"]):
        raise RegistrationError("Settings/default changed during planning; no capacity-lock write was attempted.")
    if plan["settings_changed"] and refresh_advertised is not None:
        advertised = refresh_advertised()
        fixed_inkling_candidate(registry, advertised)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with snapshot.open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 1, "provider": PROVIDER, "revision_before": plan["_revision"],
                   "models_public_before": public_models(plan["_before"]),
                   "model_ids_after": plan["model_ids_after"],
                   "rollback_note": "Public metadata only; reread current revision and preserve unrelated/custom fields before native rollback."}, stream, indent=2)
        stream.write("\n")
    if plan["settings_changed"]:
        rpc.call("settings/mutate", {"ns": "llm-pi-ai", "ops": [
            {"op": "set", "path": ["providers", PROVIDER, "models"], "value": plan["_after"]}],
            "expectedRevision": plan["_revision"]})
    verified = capacity_lock_plan(registry, advertised, rpc.call("settings/describe", {}),
                                  base_url=base_url, acknowledge_retire_short=True, output=output, context=context)
    if verified["blockers"] or verified["_before"] != plan["_after"] or verified["_default"] != plan["_default"]:
        raise RegistrationError("Capacity lock may be saved but post-save verification failed; review snapshot and live settings before further work.")
    return {"mode": "capacity-lock", "provider": PROVIDER, "settings_changed": plan["settings_changed"],
            "model_ids": plan["model_ids_after"], "default_changed": False, "credentials_changed": False}


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
    parser.add_argument("--lock-inkling-capacity", action="store_true",
                        help=f"Manage the main Inkling text+vision profile ({CAPACITY_CONTEXT} context / {CAPACITY_OUTPUT} output backend)")
    parser.add_argument("--inkling-context", type=int, choices=(CAPACITY_CONTEXT,),
                        help="Use the full verified context instead of preserving a lower saved context")
    parser.add_argument("--inkling-output", type=int, choices=(DEFAULT_CAPACITY_OUTPUT, CAPACITY_OUTPUT),
                        help=f"Capacity-lock DSH output budget (default {DEFAULT_CAPACITY_OUTPUT}; opt in to {CAPACITY_OUTPUT})")
    parser.add_argument("--ack-retire-short", action="store_true", help="Operator confirms no active/saved sessions depend on short alias; never overrides live-default guard")
    parser.add_argument("--settings-view", type=Path, help="Explicit redacted Settings describe JSON for offline lock planning only; not authoritative for apply")
    parser.add_argument("--token-file", type=Path, default=ROOT / ".runtime/local-stack/inference.token")
    parser.add_argument("--cookie-env", default="DSH_AUTH_COOKIE")
    parser.add_argument("--launch-url-env", default="DSH_LAUNCH_URL")
    parser.add_argument("--snapshot", type=Path, help="Optional public managed-field snapshot; never contains secret values")
    args = parser.parse_args(argv)
    if (args.settings_view or args.ack_retire_short or args.inkling_output is not None
            or args.inkling_context is not None) and not args.lock_inkling_capacity:
        parser.error("--settings-view / --ack-retire-short / --inkling-output / --inkling-context require --lock-inkling-capacity")
    if args.settings_view and args.apply:
        parser.error("--settings-view is planning-only; apply must read authenticated live settings")
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8-sig"))
        key = ""
        if args.apply or args.use_gateway_metadata or args.lock_inkling_capacity:
            key = os.environ.get(CREDENTIAL_REF, "")
            if not key and args.token_file.is_file():
                key = args.token_file.read_text(encoding="utf-8").strip()
            if not key:
                raise RegistrationError("LOCAL_STACK_API_KEY or the inference token file is required; control tokens are never accepted.")
        advertised = gateway_models(args.base_url, key) if (args.use_gateway_metadata or args.lock_inkling_capacity) else None
        if args.lock_inkling_capacity:
            output = args.inkling_output if args.inkling_output is not None else DEFAULT_CAPACITY_OUTPUT
            fixed_inkling_candidate(registry, advertised)
            # Without owner authentication, report a blocked candidate-only proposal.
            # Never read profile YAML, DSH credential files or signing secrets.
            rpc = None
            cookie = os.environ.get(args.cookie_env)
            launch = os.environ.get(args.launch_url_env)
            if (cookie or launch) and not args.settings_view:
                rpc = DshRpc(args.dsh_url, cookie)
                if not cookie:
                    rpc.exchange_launch_url(launch)
            if args.apply:
                if rpc is None:
                    raise RegistrationError("Capacity-lock apply requires authenticated live DSH settings/default; use the native migration plan first.")
                snapshot = args.snapshot or ROOT / ".runtime/local-stack" / (
                    "dsh-capacity-lock-before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
                result = apply_capacity_lock(rpc, registry, advertised, snapshot, base_url=args.base_url,
                                             acknowledge_retire_short=args.ack_retire_short, output=output, context=args.inkling_context,
                                             refresh_advertised=lambda: gateway_models(args.base_url, key))
            else:
                document = None
                if args.settings_view:
                    document = json.loads(args.settings_view.read_text(encoding="utf-8-sig"))
                elif rpc is not None:
                    document = rpc.call("settings/describe", {})
                result = public_capacity_plan(capacity_lock_plan(
                    registry, advertised, document, base_url=args.base_url,
                    acknowledge_retire_short=args.ack_retire_short, output=output, context=args.inkling_context))
                if args.settings_view:
                    result["settings_verified"] = False
                    result["status"] = "blocked"
                    result["blockers"].append("Offline settings snapshot is proposal evidence only; fresh live settings/default checks required for apply.")
            print(json.dumps(result, indent=2))
            return 0
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
