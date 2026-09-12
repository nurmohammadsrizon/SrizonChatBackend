from __future__ import annotations

import asyncio
from collections import defaultdict
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.connections[user_id].add(ws)

    async def disconnect(self, user_id: str, ws: WebSocket) -> None:
        async with self.lock:
            sockets = self.connections.get(user_id)
            if not sockets:
                return
            sockets.discard(ws)
            if not sockets:
                self.connections.pop(user_id, None)

    def is_online(self, user_id: str) -> bool:
        return bool(self.connections.get(user_id))

    def online_user_ids(self) -> set[str]:
        return {uid for uid, sockets in self.connections.items() if sockets}

    async def send_user(self, user_id: str, payload: dict) -> None:
        sockets = list(self.connections.get(user_id, set()))
        if not sockets:
            return
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(user_id, ws)

    async def send_users(self, user_ids: set[str], payload: dict) -> None:
        if user_ids:
            await asyncio.gather(*(self.send_user(uid, payload) for uid in user_ids))
