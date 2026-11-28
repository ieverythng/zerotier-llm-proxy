"""Entrypoints for the loopback gateway and the Rich terminal launcher."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .registry import StackError


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Watson single-model stack")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Serve the authenticated local switching gateway")
    serve.add_argument("--registry", default=str(root / "config" / "models.json"))
    serve.add_argument("--state-dir", default=str(root / ".runtime" / "local-stack"))
    serve.add_argument("--port", type=int, default=4141)
    serve.add_argument("--backend-port", type=int, default=18081)
    serve.add_argument("--upstream", default="http://127.0.0.1:18083/v1")
    ui = commands.add_parser("ui", help="Open the interactive terminal selector")
    ui.add_argument("--base-url", default="http://127.0.0.1:4141")
    ui.add_argument("--registry", default=str(root / "config" / "models.json"))
    ui.add_argument("--snapshot", action="store_true", help="Render one status frame without interactive input")
    ui.add_argument("--direct", action="store_true", help="Explicitly start/attach the direct diagnostic route")
    status = commands.add_parser("status", help="Show sanitized gateway status")
    status.add_argument("--base-url", default="http://127.0.0.1:4141")
    status.add_argument("--state-dir", default=str(root / ".runtime" / "local-stack"))
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .gateway import GatewayServer
            from .runtime import ModelRuntime
            if not all(1024 <= port <= 65535 for port in (args.port, args.backend_port)) or args.port == args.backend_port:
                raise StackError("Gateway/backend ports must be distinct nonprivileged TCP ports.")
            runtime = ModelRuntime(args.registry, args.state_dir, args.backend_port)
            server = GatewayServer(("127.0.0.1", args.port), runtime, upstream=args.upstream)
            print(f"Watson local gateway listening on http://127.0.0.1:{args.port}; one active model; no model loaded automatically.", flush=True)
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.command == "status":
            from .client import StackClient
            print(json.dumps(StackClient(args.base_url, state_dir=args.state_dir).status(), indent=2))
            return 0
        from .tui import run
        return run(base_url=getattr(args, "base_url", "http://127.0.0.1:4141"), registry_path=getattr(args, "registry", str(root / "config" / "models.json")), snapshot=getattr(args, "snapshot", False), direct=getattr(args, "direct", False)) or 0
    except StackError as exc:
        print(f"Local stack: {exc}", file=sys.stderr)
        return 1
    except ImportError:
        print("Terminal UI dependencies are missing. Run scripts/windows/Install-LocalModelStack.ps1 first.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
