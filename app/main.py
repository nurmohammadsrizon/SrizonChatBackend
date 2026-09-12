from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .json_store import now_iso, new_id, store
from .realtime import ConnectionManager
from .schemas import AuthBody, AuthResponse, DeviceRegister, MessageCreate, MessageEdit, MessageOut, ReadBody, UserOut
from .security import create_access_token, current_user, decode_access_token, hash_password, verify_password
from .services.fcm import send_chat_notification

manager = ConnectionManager()


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def public_user(user: dict, online: bool = False) -> UserOut:
    return UserOut(
        id=user["id"],
        username=user["username"],
        display_name=user["display_name"],
        created_at=parse_dt(user["created_at"]),
        last_seen_at=parse_dt(user["last_seen_at"]) if user.get("last_seen_at") else None,
        online=online,
    )


def conversation_id_for(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"dm_{x}_{y}"


async def get_or_create_dm(a: str, b: str) -> dict:
    cid = conversation_id_for(a, b)
    existing = await store.find_one("conversations", lambda c: c.get("id") == cid)
    if existing:
        return existing
    return await store.upsert("conversations", {
        "id": cid,
        "kind": "direct",
        "name": None,
        "user_a_id": a,
        "user_b_id": b,
        "created_at": now_iso(),
    })


async def recipients_for(conversation: dict, sender_id: str) -> set[str]:
    if conversation["kind"] == "public":
        users = await store.find_many("users", lambda u: u.get("is_active", True))
        return {u["id"] for u in users}
    return {x for x in (conversation.get("user_a_id"), conversation.get("user_b_id")) if x}


async def push_message_notification(recipients: set[str], sender_name: str, message: dict, conversation: dict) -> None:
    if not recipients:
        return
    devices = await store.find_many("devices", lambda d: d.get("user_id") in recipients)
    tokens = [d["token"] for d in devices if d.get("token")]
    if not tokens:
        return
    title = sender_name if conversation["kind"] == "direct" else f"{sender_name} in {conversation.get('name') or 'Public Lounge'}"
    text = "This message was deleted." if message.get("deleted") else message["text"]
    await asyncio.to_thread(
        send_chat_notification,
        tokens,
        title=title,
        body=text[:300],
        conversation_id=conversation["id"],
        message_id=message["id"],
    )


async def emit(recipients: set[str], payload: dict) -> None:
    await manager.send_users(recipients, payload)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await store.init()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    description="UI-free realtime messaging backend using JSON files. Suitable for personal/small deployments.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment, "storage": "json"}


@app.post("/api/v1/auth/signup", response_model=AuthResponse, status_code=201)
async def signup(body: AuthBody):
    username = body.username.strip().lower()
    exists = await store.find_one("users", lambda u: u.get("username", "").lower() == username)
    if exists:
        raise HTTPException(409, "Username already exists")
    user = {
        "id": new_id(),
        "username": username,
        "display_name": (body.display_name or username).strip(),
        "password_hash": hash_password(body.password),
        "created_at": now_iso(),
        "last_seen_at": None,
        "is_active": True,
    }
    await store.upsert("users", user)
    return AuthResponse(access_token=create_access_token(user["id"]), user=public_user(user))


@app.post("/api/v1/auth/login", response_model=AuthResponse)
async def login(body: AuthBody):
    username = body.username.strip().lower()
    user = await store.find_one("users", lambda u: u.get("username") == username and u.get("is_active", True))
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid username or password")
    user["last_seen_at"] = now_iso()
    await store.upsert("users", user)
    return AuthResponse(access_token=create_access_token(user["id"]), user=public_user(user, manager.is_online(user["id"])))


@app.get("/api/v1/me", response_model=UserOut)
async def me(user: dict = Depends(current_user)):
    return public_user(user, manager.is_online(user["id"]))


@app.get("/api/v1/users")
async def users(user: dict = Depends(current_user)):
    rows = await store.find_many("users", lambda u: u.get("is_active", True) and u["id"] != user["id"])
    rows.sort(key=lambda u: u["display_name"].lower())
    return {"current_user_id": user["id"], "users": [public_user(u, manager.is_online(u["id"])) for u in rows]}


@app.get("/api/v1/chats")
async def chats(user: dict = Depends(current_user)):
    others = await store.find_many("users", lambda u: u.get("is_active", True) and u["id"] != user["id"])
    others.sort(key=lambda u: u["display_name"].lower())
    group = await store.ensure_public_group()
    return {
        "personal": [public_user(u, manager.is_online(u["id"])) for u in others],
        "groups": [{"id": group["id"], "name": group["name"], "public": True}],
    }


@app.post("/api/v1/groups", status_code=403)
async def create_group_forbidden(user: dict = Depends(current_user)):
    raise HTTPException(403, "Users cannot create groups. Only the fixed Public Lounge is available.")


async def resolve_conversation(user: dict, chat_type: str, target_id: str) -> dict:
    if chat_type == "group":
        if target_id != settings.public_group_id:
            raise HTTPException(404, "Group not found")
        return await store.ensure_public_group()
    if chat_type == "personal":
        other = await store.find_one("users", lambda u: u.get("id") == target_id and u.get("is_active", True))
        if not other or other["id"] == user["id"]:
            raise HTTPException(404, "User not found")
        return await get_or_create_dm(user["id"], other["id"])
    raise HTTPException(400, "Invalid chat type")


def message_payload(message: dict, sender_display_name: str, reply: dict | None = None) -> dict:
    return {
        "id": message["id"],
        "conversation_id": message["conversation_id"],
        "sender_id": message["sender_id"],
        "sender_display_name": sender_display_name,
        "text": message["text"],
        "created_at": parse_dt(message["created_at"]),
        "reply_to": reply,
        "deleted": bool(message.get("deleted", False)),
        "edited": bool(message.get("edited", False)),
    }


@app.get("/api/v1/history/{chat_type}/{target_id}")
async def history(
    chat_type: str,
    target_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None),
    user: dict = Depends(current_user),
):
    conversation = await resolve_conversation(user, chat_type, target_id)
    rows = await store.find_many("messages", lambda m: m.get("conversation_id") == conversation["id"])
    rows.sort(key=lambda m: m["created_at"], reverse=True)
    if before:
        cursor = next((m for m in rows if m["id"] == before), None)
        if cursor:
            rows = [m for m in rows if m["created_at"] < cursor["created_at"]]
    rows = rows[:limit]
    rows.reverse()

    senders = await store.find_many("users", lambda u: u.get("id") in {m["sender_id"] for m in rows})
    names = {u["id"]: u["display_name"] for u in senders}
    reply_ids = {m.get("reply_to_id") for m in rows if m.get("reply_to_id")}
    reply_rows = await store.find_many("messages", lambda m: m.get("id") in reply_ids)
    reply_map = {m["id"]: m for m in reply_rows}

    data = []
    for message in rows:
        reply = None
        if message.get("reply_to_id") in reply_map:
            rm = reply_map[message["reply_to_id"]]
            reply = {
                "id": rm["id"],
                "sender_id": rm["sender_id"],
                "text": "This message was deleted." if rm.get("deleted") else rm["text"][:180],
            }
        data.append(message_payload(message, names.get(message["sender_id"], "Unknown"), reply))

    return {"conversation_id": conversation["id"], "messages": data, "next_before": rows[0]["id"] if len(rows) == limit else None}


@app.post("/api/v1/messages", response_model=MessageOut)
async def send_message(body: MessageCreate, user: dict = Depends(current_user)):
    conversation = await resolve_conversation(user, body.chat_type, body.target_id)
    reply = None
    if body.reply_to:
        target = await store.find_one("messages", lambda m: m.get("id") == body.reply_to and m.get("conversation_id") == conversation["id"])
        if not target or target.get("deleted"):
            raise HTTPException(400, "Reply target not found")
        sender = await store.find_one("users", lambda u: u.get("id") == target["sender_id"])
        reply = {"id": target["id"], "sender_id": target["sender_id"], "text": target["text"][:180], "sender_display_name": sender["display_name"] if sender else "Unknown"}

    message = {
        "id": new_id(),
        "conversation_id": conversation["id"],
        "sender_id": user["id"],
        "text": body.text.strip(),
        "created_at": now_iso(),
        "reply_to_id": body.reply_to,
        "deleted": False,
        "edited": False,
    }
    await store.upsert("messages", message)
    payload = message_payload(message, user["display_name"], reply)
    recipients = await recipients_for(conversation, user["id"])
    await emit(recipients, {"type": "message.created", "message": payload})
    recipients.discard(user["id"])
    await push_message_notification(recipients, user["display_name"], message, conversation)
    return MessageOut(**payload)


@app.patch("/api/v1/messages/{message_id}", response_model=MessageOut)
async def edit_message(message_id: str, body: MessageEdit, user: dict = Depends(current_user)):
    message = await store.find_one("messages", lambda m: m.get("id") == message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    if message["sender_id"] != user["id"]:
        raise HTTPException(403, "You can edit only your own messages")
    if message.get("deleted"):
        raise HTTPException(400, "Deleted message cannot be edited")
    message["text"] = body.text.strip()
    message["edited"] = True
    await store.upsert("messages", message)
    payload = message_payload(message, user["display_name"])
    conversation = await store.find_one("conversations", lambda c: c.get("id") == message["conversation_id"])
    recipients = await recipients_for(conversation, user["id"])
    await emit(recipients, {"type": "message.updated", "message": payload})
    return MessageOut(**payload)


@app.delete("/api/v1/messages/{message_id}")
async def delete_message(message_id: str, user: dict = Depends(current_user)):
    message = await store.find_one("messages", lambda m: m.get("id") == message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    if message["sender_id"] != user["id"]:
        raise HTTPException(403, "You can delete only your own messages")
    message["deleted"] = True
    message["text"] = "This message was deleted."
    await store.upsert("messages", message)
    conversation = await store.find_one("conversations", lambda c: c.get("id") == message["conversation_id"])
    recipients = await recipients_for(conversation, user["id"])
    await emit(recipients, {"type": "message.deleted", "message_id": message["id"], "conversation_id": message["conversation_id"]})
    return {"ok": True, "message_id": message["id"]}


@app.post("/api/v1/messages/read")
async def mark_read(body: ReadBody, user: dict = Depends(current_user)):
    message = await store.find_one("messages", lambda m: m.get("id") == body.message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    existing = await store.find_one("message_reads", lambda r: r.get("message_id") == message["id"] and r.get("user_id") == user["id"])
    read_at = now_iso()
    if not existing:
        await store.upsert("message_reads", {"id": new_id(), "message_id": message["id"], "user_id": user["id"], "read_at": read_at})
    conversation = await store.find_one("conversations", lambda c: c.get("id") == message["conversation_id"])
    recipients = await recipients_for(conversation, user["id"])
    await emit(recipients, {"type": "message.read", "message_id": message["id"], "user_id": user["id"], "read_at": parse_dt(read_at)})
    return {"ok": True}


@app.post("/api/v1/devices/register")
async def register_device(body: DeviceRegister, user: dict = Depends(current_user)):
    existing = await store.find_one("devices", lambda d: d.get("user_id") == user["id"] and d.get("token") == body.token)
    if existing:
        existing["platform"] = body.platform
        existing["updated_at"] = now_iso()
        await store.upsert("devices", existing)
        return {"ok": True, "device_id": existing["id"]}
    device = {"id": new_id(), "user_id": user["id"], "platform": body.platform, "token": body.token, "created_at": now_iso(), "updated_at": now_iso()}
    await store.upsert("devices", device)
    return {"ok": True, "device_id": device["id"]}


@app.delete("/api/v1/devices/{device_id}")
async def unregister_device(device_id: str, user: dict = Depends(current_user)):
    ok = await store.delete_one("devices", lambda d: d.get("id") == device_id and d.get("user_id") == user["id"])
    if not ok:
        raise HTTPException(404, "Device not found")
    return {"ok": True}


@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    user_id = decode_access_token(token)
    if not user_id:
        await websocket.close(code=1008)
        return

    user = await store.find_one("users", lambda u: u.get("id") == user_id and u.get("is_active", True))
    if not user:
        await websocket.close(code=1008)
        return

    await manager.connect(user_id, websocket)
    user["last_seen_at"] = now_iso()
    await store.upsert("users", user)

    others = {u["id"] for u in await store.find_many("users", lambda u: u.get("is_active", True) and u["id"] != user_id)}
    await emit(others, {"type": "presence.changed", "user_id": user_id, "online": True})
    await manager.send_user(user_id, {"type": "connection.ready", "user_id": user_id})

    try:
        while True:
            raw = await websocket.receive_json()
            event_type = raw.get("type")
            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif event_type in {"typing.start", "typing.stop"}:
                conversation_id = raw.get("conversation_id")
                conversation = await store.find_one("conversations", lambda c: c.get("id") == conversation_id)
                if conversation:
                    recipients = await recipients_for(conversation, user_id)
                    recipients.discard(user_id)
                    await emit(recipients, {"type": event_type, "conversation_id": conversation_id, "user_id": user_id})
            elif event_type == "presence.request":
                await websocket.send_json({"type": "presence.snapshot", "online_user_ids": list(manager.online_user_ids())})
    except WebSocketDisconnect:
        await manager.disconnect(user_id, websocket)
        if not manager.is_online(user_id):
            user["last_seen_at"] = now_iso()
            await store.upsert("users", user)
            await emit(others, {"type": "presence.changed", "user_id": user_id, "online": False, "last_seen_at": parse_dt(user["last_seen_at"])})
