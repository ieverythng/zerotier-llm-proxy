"""Registration choices and confirmation, without filesystem or gateway operations.

The wizard validates the GGUF/projector and builds an allowlisted profile first.
The gateway still authenticates, validates and persists that profile; no action
here loads a model or treats the chosen template as proof of compatibility.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .registry import normalize_context

PROVISIONAL_CONTEXT_CEILING = 8192


@dataclass(frozen=True)
class RegistrationOption:
    """A template and the aligned context range it can offer a new artifact."""

    template: Mapping[str, Any] = field(repr=False)
    minimum_context: int
    maximum_context: int


def registration_options(
    templates: Iterable[Mapping[str, Any]],
    context_limit: Callable[[Mapping[str, Any]], int],
) -> list[RegistrationOption]:
    """Keep the caller's catalog order; omit unusable profiles before prompting."""
    options = []
    for template in templates:
        model_id, engine = template.get('id'), template.get('engine')
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(engine, str) or not engine.strip():
            continue
        minimum = template.get('minimum_context', 512)
        if type(minimum) is not int or minimum < 1:
            continue
        maximum = min(PROVISIONAL_CONTEXT_CEILING, context_limit(template)) // 256 * 256
        minimum = normalize_context(max(512, minimum))
        if minimum <= maximum:
            options.append(RegistrationOption(template, minimum, maximum))
    return options


@dataclass(frozen=True)
class RegistrationDraft:
    """Bind the human-readable review to the one payload that can be confirmed."""

    profile_id: str
    _snapshot: str = field(repr=False)

    @classmethod
    def capture(cls, option: RegistrationOption, model: Mapping[str, Any]) -> RegistrationDraft:
        """Capture an already validated wizard payload, not arbitrary input."""
        context = model.get('context')
        if type(context) is not int or context % 256 or not option.minimum_context <= context <= option.maximum_context:
            raise ValueError('The registration draft exceeds the selected provisional context range.')
        if model.get('engine') != option.template['engine']:
            raise ValueError('The registration draft does not use the selected engine.')
        if model.get('validation') != 'unverified' or model.get('context_verified') is not False:
            raise ValueError('New artifacts must be registered as unverified.')
        return cls(option.template['id'], json.dumps(dict(model), allow_nan=False))

    def review_fields(self) -> tuple[tuple[str, str], ...]:
        """Return plain fields; the terminal adapter still sanitizes/renders them."""
        model = json.loads(self._snapshot)
        fields = [
            ('Name', model['name']),
            ('ID', model['id']),
            ('Artifact', model['path']),
            ('Context', f"{model['context']:,} (unverified)"),
            ('Max output', f"{model['max_output_tokens']:,}"),
            ('Input', ', '.join(model.get('input', ['text']))),
            ('Inherited profile', self.profile_id),
        ]
        if model.get('mmproj'):
            fields.append(('Projector', model['mmproj']))
        return tuple(fields)

    def confirmed_payload(self, choice: str) -> dict[str, Any] | None:
        """An exact REGISTER confirms this snapshot; everything else cancels."""
        return json.loads(self._snapshot) if choice == 'REGISTER' else None
