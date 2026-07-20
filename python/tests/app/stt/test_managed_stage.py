"""Contract tests for the authenticated managed STT adapter."""

from __future__ import annotations

import asyncio
import queue
import unittest
from collections.abc import AsyncIterator, Coroutine

from app.audio.base import AudioFrame
from app.core.messages import OutgoingMessage, SttInterimMsg
from app.services.managed_session import ManagedSessionStore
from app.stt.stages import stt_managed
from app.stt.stages.stt_managed import ManagedSttStage


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[OutgoingMessage] = []
        self.tasks: list[asyncio.Task[object]] = []

    def publish(self, msg: OutgoingMessage) -> None:
        self.messages.append(msg)

    def schedule(self, coro: Coroutine[object, object, object]) -> None:
        self.tasks.append(asyncio.create_task(coro))


class _Socket:
    def __init__(self, incoming: list[str | bytes] | None = None) -> None:
        self.sent: list[bytes] = []
        self._incoming: list[str | bytes] = incoming or []

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        async def messages() -> AsyncIterator[str | bytes]:
            for message in self._incoming:
                yield message

        return messages()


class ManagedSttStageTest(unittest.IsolatedAsyncioTestCase):
    def _stage(
        self,
        publisher: _Publisher,
        received: list[tuple[str, str]],
    ) -> ManagedSttStage:
        async def handle_speech(role: str, text: str) -> None:
            received.append((role, text))

        return ManagedSttStage(
            queue.Queue[AudioFrame | None](),
            "self",
            publisher,
            handle_speech,
            ManagedSessionStore("managed-session-capability-00000000"),
            lambda: "session_123",
        )

    def test_relay_url_matches_worker_websocket_endpoint(self) -> None:
        url = stt_managed._relay_url(  # pyright: ignore[reportPrivateUsage]
            "https://managed.example/service",
            "session_123",
            "other",
        )

        self.assertEqual(
            "wss://managed.example/service/v1/stt?session_id=session_123&role=other",
            url,
        )

    async def test_send_loop_coalesces_pcm_into_exact_relay_frames(self) -> None:
        publisher = _Publisher()
        stage = self._stage(publisher, [])
        socket = _Socket()
        audio: queue.Queue[bytes | None] = queue.Queue()
        audio.put(b"a" * 1_600)
        audio.put(b"b" * 1_600)
        audio.put(None)

        await stage._send_loop(socket, audio)  # pyright: ignore[reportPrivateUsage]

        self.assertEqual([b"a" * 1_600 + b"b" * 1_600], socket.sent)

    async def test_recv_loop_emits_interim_and_combined_final_transcript(self) -> None:
        publisher = _Publisher()
        received: list[tuple[str, str]] = []
        stage = self._stage(publisher, received)
        socket = _Socket(
            [
                b"ignored binary",
                '{"type":"Results","is_final":false,"channel":{"alternatives":[{"transcript":" 途中 "}]}}',
                '{"type":"Results","is_final":true,"speech_final":false,"channel":{"alternatives":[{"transcript":"承知"}]}}',
                '{"type":"Results","is_final":true,"speech_final":true,"channel":{"alternatives":[{"transcript":"しました"}]}}',
            ]
        )

        await stage._recv_loop(socket)  # pyright: ignore[reportPrivateUsage]
        _ = await asyncio.gather(*publisher.tasks)

        self.assertEqual([SttInterimMsg(role="self", text="途中")], publisher.messages)
        self.assertEqual([("self", "承知しました")], received)


if __name__ == "__main__":
    _ = unittest.main()
