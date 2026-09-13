from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = None


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
        # `payload` often contains raw datetime values (created_at, read_at,
        # last_seen_at). Starlette's ws.send_json() calls json.dumps() with no
        # default= handler, so a datetime raises TypeError, which the except
        # below silently swallows as a "dead" socket -- disconnecting a client
        # that never actually dropped, killing realtime for it until reconnect.
        # jsonable_encoder() is the same converter FastAPI uses for response
        # models, so timestamps come out formatted identically to the REST API.
        text = json.dumps(jsonable_encoder(payload), separators=(",", ":"), ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(user_id, ws)

    async def send_users(self, user_ids: set[str], payload: dict) -> None:
        await asyncio.gather(*(self.send_user(uid, payload) for uid in user_ids))


class RedisBroker:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.redis = Redis.from_url(redis_url, decode_responses=True) if Redis and redis_url else None
        self.channel = "srizon-message:events"
        self.task: asyncio.Task | None = None

    async def publish(self, payload: dict) -> None:
        if self.redis:
            await self.redis.publish(self.channel, json.dumps(payload))

    async def start(self, manager: ConnectionManager) -> None:
        if not self.redis:
            return
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel)

        async def loop() -> None:
            async for item in pubsub.listen():
                if item.get("type") != "message":
                    continue
                try:
                    await self._dispatch(manager, json.loads(item["data"]))
                except Exception:
                    continue

        self.task = asyncio.create_task(loop())

    async def _dispatch(self, manager: ConnectionManager, event: dict) -> None:
        recipients = set(event.get("recipients", []))
        payload = event.get("payload", {})
        if recipients:
            await manager.send_users(recipients, payload)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
        if self.redis:
            await self.redis.close()
