"""Fence the OpenAI completion sentinel, without decoding or rewriting normal SSE."""
from __future__ import annotations

import re
import time

# Consecutive SSE line endings, without interpreting CRLF as two line endings.
_FRAME_END = re.compile(rb"(?:\r\n|\r(?!\n)|\n){2}")
_LINE_END = re.compile(rb"\r\n|\r|\n")
MAX_FRAME_BYTES = 256 * 1024
MAX_TAIL_BYTES = 16 * 1024
DRAIN_TIMEOUT_SECONDS = 1.0


class SseTerminationError(OSError):
    """The upstream did not complete a verified SSE response."""


class SseTerminalFence:
    """Forward complete records unchanged and retain only the final sentinel/tail."""

    def __init__(self):
        self.pending = bytearray()
        self.search_from = 0
        self.terminal = None
        self.at_start = True

    def feed(self, chunk):
        if self.terminal is not None:
            self._append_tail(chunk)
            return
        self.pending.extend(chunk)
        offset = 0
        while match := _FRAME_END.search(self.pending, self.search_from):
            end = match.end()
            # A trailing CR could be the start of a split CRLF delimiter.
            if end == len(self.pending) and self.pending[-1] == 13:
                break
            if end - offset > MAX_FRAME_BYTES:
                raise SseTerminationError('SSE record exceeded its framing bound')
            frame = bytes(self.pending[offset:end])
            parsed = frame.removeprefix(b'\xef\xbb\xbf') if self.at_start else frame
            self.at_start = False
            data = [line[5:].removeprefix(b' ') if line.startswith(b'data:') else b''
                    for line in _LINE_END.split(parsed) if line == b'data' or line.startswith(b'data:')]
            if data == [b'[DONE]']:
                self.terminal = bytearray(frame)
                self._append_tail(self.pending[end:])
                self.pending.clear()
                return
            offset = end
            self.search_from = end
            yield frame
        if offset:
            del self.pending[:offset]
        if len(self.pending) > MAX_FRAME_BYTES:
            raise SseTerminationError('SSE record exceeded its framing bound')
        self.search_from = max(0, len(self.pending) - 3)

    def _append_tail(self, chunk):
        if len(self.terminal) + len(chunk) > MAX_TAIL_BYTES:
            raise SseTerminationError('SSE trailer exceeded its bound')
        self.terminal.extend(chunk)

    def finish(self, upstream):
        if self.terminal is None or (type(getattr(upstream, 'length', None)) is int and upstream.length > 0):
            raise SseTerminationError('SSE stream ended without a complete terminal record')
        # The trailer may contain comments and separators, but no new events.
        separator = _FRAME_END.search(self.terminal)
        trailer = self.terminal[separator.end():]
        if any(line and not line.startswith(b':') for line in _LINE_END.split(trailer)):
            raise SseTerminationError('Unexpected data after SSE completion')
        return bytes(self.terminal)


def tighten_drain_timeout(upstream, deadline):
    """Apply the short tail timeout only after DONE, to stdlib's loopback response."""
    if callable(getattr(upstream, 'isclosed', None)) and upstream.isclosed():
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SseTerminationError('Timed out waiting for SSE transport closure')
    # urllib's http.client.HTTPResponse owns the only upstream connection here.
    # Do not silently fall back to the much longer normal-stream socket timeout.
    sock = getattr(getattr(getattr(upstream, 'fp', None), 'raw', None), '_sock', None)
    if not callable(getattr(sock, 'settimeout', None)):
        raise SseTerminationError('Cannot set a bounded SSE trailer read timeout')
    sock.settimeout(remaining)
