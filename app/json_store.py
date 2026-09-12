from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import settings


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


class JsonStore:
    """Small file-backed JSON datastore intended for personal/small deployments."""

    def __init__(self, root: str = "./data") -> None:
        self.root = Path(root)
        self.lock = asyncio.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "users": self.root / "users.json",
            "conversations": self.root / "conversations.json",
            "messages": self.root / "messages.json",
            "message_reads": self.root / "message_reads.json",
            "devices": self.root / "devices.json",
        }

    def _default(self, name: str) -> list[dict[str, Any]]:
        return []

    def _load_sync(self, name: str) -> list[dict[str, Any]]:
        path = self.paths[name]
        if not path.exists():
            path.write_text("[]\n", encoding="utf-8")
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else self._default(name)
        except (json.JSONDecodeError, OSError):
            return self._default(name)

    def _write_sync(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self.paths[name]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    async def init(self) -> None:
        async with self.lock:
            for name in self.paths:
                if not self.paths[name].exists():
                    self._write_sync(name, [])
            conversations = self._load_sync("conversations")
            if not any(c.get("id") == settings.public_group_id for c in conversations):
                conversations.append({
                    "id": settings.public_group_id,
                    "kind": "public",
                    "name": settings.public_group_name,
                    "user_a_id": None,
                    "user_b_id": None,
                    "created_at": now_iso(),
                })
                self._write_sync("conversations", conversations)

    async def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        async with self.lock:
            return {name: deepcopy(self._load_sync(name)) for name in self.paths}

    async def find_one(self, name: str, predicate) -> dict[str, Any] | None:
        async with self.lock:
            rows = self._load_sync(name)
            for row in rows:
                if predicate(row):
                    return deepcopy(row)
        return None

    async def find_many(self, name: str, predicate=None) -> list[dict[str, Any]]:
        async with self.lock:
            rows = self._load_sync(name)
            selected = rows if predicate is None else [r for r in rows if predicate(r)]
            return deepcopy(selected)

    async def upsert(self, name: str, row: dict[str, Any], key: str = "id") -> dict[str, Any]:
        async with self.lock:
            rows = self._load_sync(name)
            for idx, existing in enumerate(rows):
                if existing.get(key) == row.get(key):
                    rows[idx] = deepcopy(row)
                    self._write_sync(name, rows)
                    return deepcopy(row)
            rows.append(deepcopy(row))
            self._write_sync(name, rows)
            return deepcopy(row)

    async def update_one(self, name: str, predicate, updater) -> dict[str, Any] | None:
        async with self.lock:
            rows = self._load_sync(name)
            for idx, row in enumerate(rows):
                if predicate(row):
                    new_row = updater(deepcopy(row))
                    rows[idx] = deepcopy(new_row)
                    self._write_sync(name, rows)
                    return deepcopy(new_row)
        return None

    async def delete_one(self, name: str, predicate) -> bool:
        async with self.lock:
            rows = self._load_sync(name)
            for idx, row in enumerate(rows):
                if predicate(row):
                    rows.pop(idx)
                    self._write_sync(name, rows)
                    return True
        return False

    async def ensure_public_group(self) -> dict[str, Any]:
        conversation = await self.find_one("conversations", lambda c: c.get("id") == settings.public_group_id)
        if conversation:
            return conversation
        row = {
            "id": settings.public_group_id,
            "kind": "public",
            "name": settings.public_group_name,
            "user_a_id": None,
            "user_b_id": None,
            "created_at": now_iso(),
        }
        return await self.upsert("conversations", row)


store = JsonStore()
