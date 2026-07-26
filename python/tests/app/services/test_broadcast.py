import unittest
from typing import cast

from fastapi import WebSocket

from app.core.messages import StatusMsg
from app.core.types import BroadcastPayload
from app.services.broadcast import BroadcastManager


class _DisconnectingWebSocket:
    def __init__(self, manager: BroadcastManager) -> None:
        self._manager: BroadcastManager = manager
        self.messages: list[object] = []

    async def send_json(self, data: object) -> None:
        self.messages.append(data)
        self._manager.connections.discard(cast(WebSocket, cast(object, self)))


class BroadcastManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_tolerates_disconnects_during_send(self) -> None:
        manager = BroadcastManager()
        clients = [_DisconnectingWebSocket(manager) for _ in range(2)]
        manager.connections.update(cast(WebSocket, cast(object, client)) for client in clients)

        await manager.broadcast(StatusMsg(text="hello"))

        self.assertEqual(manager.connections, set())
        self.assertEqual(
            [client.messages for client in clients],
            [[{"type": "status", "text": "hello"}]] * 2,
        )

    async def test_broadcast_raw_tolerates_disconnects_during_send(self) -> None:
        manager = BroadcastManager()
        clients = [_DisconnectingWebSocket(manager) for _ in range(2)]
        manager.connections.update(cast(WebSocket, cast(object, client)) for client in clients)
        payload: BroadcastPayload = {"type": "status", "text": "hello"}

        await manager.broadcast_raw(payload)

        self.assertEqual(manager.connections, set())
        self.assertEqual([client.messages for client in clients], [[payload]] * 2)


if __name__ == "__main__":
    _ = unittest.main()
