"""Read-only dashboard view of a fresh, identified native experiment worker."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .runtime import process_identity, process_parent_pid

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / '.runtime' / 'inkling-qualification' / 'active-worker.json'
ENGINES = ROOT / '.runtime' / 'local-model-deps'
PHASES = {'load': 'loading', 'calibration': 'tokenizing synthetic input',
          'chat_quality': 'synthetic long-chat check', 'vision_canary': 'synthetic vision check'}


def observe_scratch(path=RECEIPT, *, now=None, identify=process_identity, parent_of=process_parent_pid):
    """No endpoints, commands, credentials, process scanning, or lifecycle claims."""
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            return None
        with path.open('rb') as f:
            raw = f.read(4097)
        if len(raw) > 4096:
            return None
        rec = json.loads(raw)
        if not isinstance(rec, dict):
            return None
        if type(rec.get('version')) is not int or rec['version'] != 1 or rec.get('alias') != 'inkling2-mini':
            return None
        if rec.get('port') != 18181 or type(rec.get('ready')) is not bool:
            return None
        context = rec.get('context')
        if type(context) is not int or not 4096 <= context <= 200192 or context % 256:
            return None
        ts = datetime.fromisoformat(rec['observed_at'])
        if ts.tzinfo is None or not 0 <= ((now or datetime.now(timezone.utc)) - ts).total_seconds() <= 8:
            return None
        worker, runner = rec['worker'], rec['runner']
        worker_pid, runner_pid = worker['pid'], runner['pid']
        if any(type(p) is not int or p <= 0 for p in (worker_pid, runner_pid)) or worker_pid == runner_pid:
            return None
        executable = Path(worker['identity']['executable']).resolve()
        if executable.name.lower() not in {'llama-server.exe','llama-server'} or not executable.is_relative_to(ENGINES.resolve()):
            return None
        if identify(worker_pid) != worker['identity'] or identify(runner_pid) != runner['identity'] or parent_of(worker_pid) != runner_pid:
            return None
        phase = rec['phase']
        text = PHASES.get(phase) if isinstance(phase, str) else None
        if text is None and isinstance(phase, str) and (match := re.fullmatch(r'prompt_(\d{1,6})', phase)):
            size = int(match[1]); text = f'{size:,}-token timing request' if 32 <= size < context else None
        if text is None:
            return None
        return {'source': 'verified_scratch', 'alias': 'inkling2-mini', 'context': context,
                'phase': text, 'observed_at': ts.isoformat(), 'pid': worker_pid, 'ready': rec['ready']}
    except (OSError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return None
