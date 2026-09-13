from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import and_, or_, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import SessionLocal, init_db
from .models import Conversation, Device, Message, MessageRead, User, now_utc
from .realtime import ConnectionManager, RedisBroker
from .schemas import AuthBody, AuthResponse, DeviceRegister, MessageCreate, MessageEdit, MessageOut, ReadBody, UserOut
from .security import create_access_token, current_user, get_session, hash_password, decode_access_token, verify_password
from .services.fcm import send_chat_notification

manager = ConnectionManager()
broker = RedisBroker(settings.redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        public = await session.scalar(select(Conversation).where(Conversation.id == settings.public_group_id))
        if not public:
            session.add(Conversation(id=settings.public_group_id, kind="public", name=settings.public_group_name))
            await session.commit()
    await broker.start(manager)
    yield
    await broker.stop()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="UI-free realtime messaging backend for Android, iOS, web, desktop and other clients.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    	"http://localhost:8080/",
	    "http://localhost:8080",
        "https://srizonchatfrontend.onrender.com/"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def public_user(user: User, online: bool = False) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        online=online,
    )


def conversation_id_for(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"dm_{x}_{y}"


async def get_or_create_dm(session: AsyncSession, a: str, b: str) -> Conversation:
    cid = conversation_id_for(a, b)
    conversation = await session.scalar(select(Conversation).where(Conversation.id == cid))
    if not conversation:
        conversation = Conversation(id=cid, kind="direct", user_a_id=a, user_b_id=b)
        session.add(conversation)
        await session.flush()
    return conversation


async def recipients_for(session: AsyncSession, conversation: Conversation, sender_id: str) -> set[str]:
    if conversation.kind == "public":
        users = (await session.scalars(select(User.id).where(User.is_active.is_(True)))).all()
        return set(users)
    return {x for x in (conversation.user_a_id, conversation.user_b_id) if x}


async def push_message_notification(session: AsyncSession, recipients: set[str], sender_name: str, message: Message, conversation: Conversation) -> None:
    if not recipients:
        return
    rows = (await session.scalars(select(Device.token).where(Device.user_id.in_(recipients)))).all()
    if not rows:
        return
    title = sender_name if conversation.kind == "direct" else f"{sender_name} in {conversation.name or 'Public Lounge'}"
    text = "This message was deleted." if message.deleted else message.text
    # FCM send is blocking; run it off the event loop.
    import asyncio
    await asyncio.to_thread(
        send_chat_notification,
        rows,
        title=title,
        body=text[:300],
        conversation_id=conversation.id,
        message_id=message.id,
    )


async def emit(recipients: set[str], payload: dict) -> None:
    event = {"recipients": list(recipients), "payload": payload}
    if settings.redis_url:
        await broker.publish(event)
    else:
        await manager.send_users(recipients, payload)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}


@app.post("/api/v1/auth/signup", response_model=AuthResponse, status_code=201)
async def signup(body: AuthBody, session: AsyncSession = Depends(get_session)):
    username = body.username.strip().lower()
    exists = await session.scalar(select(User).where(User.username.ilike(username)))
    if exists:
        raise HTTPException(409, "Username already exists")
    user = User(username=username, display_name=(body.display_name or username).strip(), password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return AuthResponse(access_token=create_access_token(user.id), user=public_user(user))


@app.post("/api/v1/auth/login", response_model=AuthResponse)
async def login(body: AuthBody, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.username == body.username.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    user.last_seen_at = now_utc()
    await session.commit()
    return AuthResponse(access_token=create_access_token(user.id), user=public_user(user))


@app.get("/api/v1/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return public_user(user, manager.is_online(user.id))


@app.get("/api/v1/users")
async def users(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(User).where(User.is_active.is_(True), User.id != user.id).order_by(User.display_name))).all()
    return {"current_user_id": user.id, "users": [public_user(u, manager.is_online(u.id)) for u in rows]}


@app.get("/api/v1/chats")
async def chats(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    others = (await session.scalars(select(User).where(User.is_active.is_(True), User.id != user.id).order_by(User.display_name))).all()
    group = await session.scalar(select(Conversation).where(Conversation.id == settings.public_group_id))
    return {
        "personal": [public_user(u, manager.is_online(u.id)) for u in others],
        "groups": [{"id": group.id, "name": group.name, "public": True}] if group else [],
    }


@app.post("/api/v1/groups", status_code=403)
async def create_group_forbidden(user: User = Depends(current_user)):
    raise HTTPException(403, "Users cannot create groups. Only the fixed Public Lounge is available.")


async def resolve_conversation(session: AsyncSession, user: User, chat_type: str, target_id: str) -> Conversation:
    if chat_type == "group":
        if target_id != settings.public_group_id:
            raise HTTPException(404, "Group not found")
        conversation = await session.scalar(select(Conversation).where(Conversation.id == settings.public_group_id, Conversation.kind == "public"))
    elif chat_type == "personal":
        other = await session.get(User, target_id)
        if not other or not other.is_active or other.id == user.id:
            raise HTTPException(404, "User not found")
        conversation = await get_or_create_dm(session, user.id, other.id)
    else:
        raise HTTPException(400, "Invalid chat type")
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    return conversation


def message_payload(message: Message, sender_display_name: str, reply: dict | None = None) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "sender_display_name": sender_display_name,
        "text": message.text,
        "created_at": message.created_at,
        "reply_to": reply,
        "deleted": message.deleted,
        "edited": message.edited,
    }


@app.get("/api/v1/history/{chat_type}/{target_id}")
async def history(
    chat_type: str,
    target_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: str | None = Query(default=None),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    conversation = await resolve_conversation(session, user, chat_type, target_id)
    query = select(Message).where(Message.conversation_id == conversation.id).order_by(desc(Message.created_at)).limit(limit)
    if before:
        cursor_message = await session.get(Message, before)
        if cursor_message and cursor_message.conversation_id == conversation.id:
            query = select(Message).where(Message.conversation_id == conversation.id, Message.created_at < cursor_message.created_at).order_by(desc(Message.created_at)).limit(limit)
    rows = list((await session.scalars(query)).all())
    rows.reverse()

    sender_ids = {m.sender_id for m in rows}
    senders = (await session.scalars(select(User).where(User.id.in_(sender_ids)))).all() if sender_ids else []
    names = {u.id: u.display_name for u in senders}
    reply_ids = {m.reply_to_id for m in rows if m.reply_to_id}
    reply_rows = (await session.scalars(select(Message).where(Message.id.in_(reply_ids)))).all() if reply_ids else []
    reply_map = {m.id: m for m in reply_rows}

    data = []
    for m in rows:
        reply = None
        if m.reply_to_id and m.reply_to_id in reply_map:
            rm = reply_map[m.reply_to_id]
            reply = {"id": rm.id, "sender_id": rm.sender_id, "text": "This message was deleted." if rm.deleted else rm.text[:180]}
        data.append(message_payload(m, names.get(m.sender_id, "Unknown"), reply))
    return {"conversation_id": conversation.id, "messages": data, "next_before": rows[0].id if len(rows) == limit else None}


@app.post("/api/v1/messages", response_model=MessageOut)
async def send_message(body: MessageCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = await resolve_conversation(session, user, body.chat_type, body.target_id)
    reply = None
    if body.reply_to:
        target = await session.get(Message, body.reply_to)
        if not target or target.conversation_id != conversation.id or target.deleted:
            raise HTTPException(400, "Reply target not found")
        target_sender = await session.get(User, target.sender_id)
        reply = {"id": target.id, "sender_id": target.sender_id, "text": target.text[:180], "sender_display_name": target_sender.display_name if target_sender else "Unknown"}

    message = Message(conversation_id=conversation.id, sender_id=user.id, text=body.text.strip(), reply_to_id=body.reply_to)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    payload = message_payload(message, user.display_name, reply)
    recipients = await recipients_for(session, conversation, user.id)
    await emit(recipients, {"type": "message.created", "message": payload})
    recipients.discard(user.id)
    await push_message_notification(session, recipients, user.display_name, message, conversation)
    return MessageOut(**payload)


@app.patch("/api/v1/messages/{message_id}", response_model=MessageOut)
async def edit_message(message_id: str, body: MessageEdit, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    message = await session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    if message.sender_id != user.id:
        raise HTTPException(403, "You can edit only your own messages")
    if message.deleted:
        raise HTTPException(400, "Deleted message cannot be edited")
    message.text = body.text.strip()
    message.edited = True
    await session.commit()
    payload = message_payload(message, user.display_name)
    conversation = await session.get(Conversation, message.conversation_id)
    recipients = await recipients_for(session, conversation, user.id)
    await emit(recipients, {"type": "message.updated", "message": payload})
    return MessageOut(**payload)


@app.delete("/api/v1/messages/{message_id}")
async def delete_message(message_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    message = await session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    if message.sender_id != user.id:
        raise HTTPException(403, "You can delete only your own messages")
    message.deleted = True
    message.text = "This message was deleted."
    await session.commit()
    conversation = await session.get(Conversation, message.conversation_id)
    recipients = await recipients_for(session, conversation, user.id)
    await emit(recipients, {"type": "message.deleted", "message_id": message.id, "conversation_id": message.conversation_id})
    return {"ok": True, "message_id": message.id}


@app.post("/api/v1/messages/read")
async def mark_read(body: ReadBody, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    message = await session.get(Message, body.message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    existing = await session.scalar(select(MessageRead).where(MessageRead.message_id == message.id, MessageRead.user_id == user.id))
    if not existing:
        session.add(MessageRead(message_id=message.id, user_id=user.id))
        await session.commit()
    conversation = await session.get(Conversation, message.conversation_id)
    recipients = await recipients_for(session, conversation, user.id)
    await emit(recipients, {"type": "message.read", "message_id": message.id, "user_id": user.id, "read_at": now_utc()})
    return {"ok": True}


@app.post("/api/v1/devices/register")
async def register_device(body: DeviceRegister, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    device = await session.scalar(select(Device).where(Device.user_id == user.id, Device.token == body.token))
    if device:
        device.platform = body.platform
        device.updated_at = now_utc()
    else:
        device = Device(user_id=user.id, platform=body.platform, token=body.token)
        session.add(device)
    await session.commit()
    return {"ok": True, "device_id": device.id}


@app.delete("/api/v1/devices/{device_id}")
async def unregister_device(device_id: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    device = await session.scalar(select(Device).where(Device.id == device_id, Device.user_id == user.id))
    if not device:
        raise HTTPException(404, "Device not found")
    await session.delete(device)
    await session.commit()
    return {"ok": True}


@app.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    user_id = decode_access_token(token)
    if not user_id:
        await websocket.close(code=1008)
        return

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if not user or not user.is_active:
            await websocket.close(code=1008)
            return

        await manager.connect(user_id, websocket)
        user.last_seen_at = now_utc()
        await session.commit()

        await emit(set(), {"type": "noop"}) if False else None
        # Presence is broadcast locally; Redis deployments should use the same event path from this process.
        presence = {"type": "presence.changed", "user_id": user_id, "online": True}
        others = set((await session.scalars(select(User.id).where(User.is_active.is_(True), User.id != user_id))).all())
        await emit(others, presence)
        await manager.send_user(user_id, {"type": "connection.ready", "user_id": user_id})

        try:
            while True:
                raw = await websocket.receive_json()
                event_type = raw.get("type")
                if event_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif event_type in {"typing.start", "typing.stop"}:
                    conversation_id = raw.get("conversation_id")
                    if conversation_id:
                        conv = await session.get(Conversation, conversation_id)
                        if conv:
                            recipients = await recipients_for(session, conv, user_id)
                            recipients.discard(user_id)
                            await emit(recipients, {"type": event_type, "conversation_id": conversation_id, "user_id": user_id})
                elif event_type == "presence.request":
                    online = list(manager.online_user_ids())
                    await websocket.send_json({"type": "presence.snapshot", "online_user_ids": online})
        except WebSocketDisconnect:
            await manager.disconnect(user_id, websocket)
            if not manager.is_online(user_id):
                user.last_seen_at = now_utc()
                await session.commit()
                await emit(others, {"type": "presence.changed", "user_id": user_id, "online": False, "last_seen_at": user.last_seen_at})
