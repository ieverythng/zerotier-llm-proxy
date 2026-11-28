"""OpenAI SSE sentinel framing and bounded tail helpers; no network."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from local_stack.sse_terminal import (
    MAX_FRAME_BYTES, MAX_TAIL_BYTES, SseTerminalFence, SseTerminationError, tighten_drain_timeout,
)


class SseTerminalFenceTests(unittest.TestCase):
    def test_fragmented_sentinel_and_crlf_are_preserved_at_every_split(self):
        text = b'data: {"choices":[{"delta":{"content":"[DONE]"}}]}\r\n\r\n'
        usage = b'data: {"choices":[],"usage":{"completion_tokens":19}}\n\n'
        done = b': comment\r\ndata:[DONE]\r\n\r\n'
        keepalive = b': tail comment\r\n\r\n'
        wire = text + usage + done + keepalive
        for step in range(1, len(wire) + 1):
            with self.subTest(chunk_size=step):
                fence = SseTerminalFence()
                frames = [frame for i in range(0,len(wire),step) for frame in fence.feed(wire[i:i+step])]
                self.assertEqual(b''.join(frames), text+usage)
                self.assertEqual(fence.finish(SimpleNamespace(length=0)), done+keepalive)

    def test_first_event_bom_cannot_bypass_terminal_fence(self):
        fence = SseTerminalFence()
        wire = b'\xef\xbb\xbfdata: [DONE]\n\n'
        self.assertEqual(list(fence.feed(wire)), [])
        self.assertEqual(fence.finish(SimpleNamespace(length=0)), wire)

    def test_non_exact_events_cannot_signal_completion(self):
        for wire in (b'data: [DONE] \n\n', b'data: [DONE]\ndata:\n\n', b'data: [DONE]\n', b'data: [done]\n\n'):
            fence = SseTerminalFence()
            list(fence.feed(wire))
            with self.assertRaises(SseTerminationError):
                fence.finish(SimpleNamespace(length=None))

    def test_data_after_sentinel_or_short_http_body_refuses_completion(self):
        fence = SseTerminalFence()
        self.assertEqual(list(fence.feed(b'data: [DONE]\n\ndata: {"unexpected":true}\n\n')), [])
        with self.assertRaises(SseTerminationError):
            fence.finish(SimpleNamespace(length=0))
        fence = SseTerminalFence()
        list(fence.feed(b'data: [DONE]\n\n'))
        with self.assertRaises(SseTerminationError):
            fence.finish(SimpleNamespace(length=3))

    def test_sse_fence_memory_is_bounded(self):
        with self.assertRaises(SseTerminationError):
            list(SseTerminalFence().feed(b'x' * (MAX_FRAME_BYTES+1)))
        fence = SseTerminalFence()
        list(fence.feed(b'data: [DONE]\n\n'))
        with self.assertRaises(SseTerminationError):
            list(fence.feed(b'x' * MAX_TAIL_BYTES))

    def test_post_done_read_requires_socket_or_already_closed_response(self):
        sock = Mock()
        response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock)))
        with patch('local_stack.sse_terminal.time.monotonic', return_value=3.5):
            tighten_drain_timeout(response,4)
            sock.settimeout.assert_called_once_with(.5)
            with self.assertRaises(SseTerminationError):
                tighten_drain_timeout(response,3)
            with self.assertRaises(SseTerminationError):
                tighten_drain_timeout(SimpleNamespace(),4)
            tighten_drain_timeout(SimpleNamespace(isclosed=lambda: True),4)
