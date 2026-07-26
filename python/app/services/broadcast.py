from fastapi import WebSocket

from app.core.messages import OutgoingMessage
from app.core.types import BroadcastPayload


class BroadcastManager:
    """Owns the set of active WebSocket connections and sends messages to them."""

    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def broadcast(self, msg: OutgoingMessage) -> None:
        """Send a typed outgoing message to all connected clients."""
        payload = msg.model_dump()
        dead: set[WebSocket] = set()
        for ws in tuple(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self.connections -= dead

    async def broadcast_raw(self, msg: BroadcastPayload) -> None:
        """Send a raw dict payload — for use by STT streams that bypass the typed layer."""
        dead: set[WebSocket] = set()
        for ws in tuple(self.connections):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self.connections -= dead

    async def reply(self, ws: WebSocket, msg: OutgoingMessage) -> None:
        """Send a typed outgoing message to a single client (the requester)."""
        await ws.send_json(msg.model_dump())
