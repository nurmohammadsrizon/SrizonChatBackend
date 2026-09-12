from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str
    created_at: datetime
    last_seen_at: datetime | None = None
    online: bool = False


class AuthBody(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=50)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MessageCreate(BaseModel):
    chat_type: str = Field(pattern=r"^(group|personal)$")
    target_id: str
    text: str = Field(min_length=1, max_length=5000)
    reply_to: str | None = None


class MessageEdit(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    sender_display_name: str
    text: str
    created_at: datetime
    reply_to: dict | None = None
    deleted: bool
    edited: bool


class DeviceRegister(BaseModel):
    platform: str = Field(pattern=r"^(android|ios|web|windows|linux|macos)$")
    token: str = Field(min_length=1, max_length=500)


class ReadBody(BaseModel):
    message_id: str


class WSClientEvent(BaseModel):
    type: str
    conversation_id: str | None = None
    message_id: str | None = None
