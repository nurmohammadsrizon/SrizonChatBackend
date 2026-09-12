from __future__ import annotations

import json
import os
from typing import Iterable

from firebase_admin import credentials, initialize_app, messaging, get_app
from firebase_admin.exceptions import FirebaseError

_initialized = False


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    try:
        get_app()
        _initialized = True
        return
    except ValueError:
        pass

    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    path = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE")
    if raw:
        info = json.loads(raw)
        initialize_app(credentials.Certificate(info))
    elif path:
        initialize_app(credentials.Certificate(path))
    else:
        raise RuntimeError("Firebase credentials are not configured")
    _initialized = True


def send_chat_notification(tokens: Iterable[str], *, title: str, body: str, conversation_id: str, message_id: str) -> int:
    token_list = list(dict.fromkeys(t for t in tokens if t))
    if not token_list:
        return 0
    try:
        _ensure_initialized()
    except Exception:
        return 0

    delivered = 0
    for i in range(0, len(token_list), 500):
        chunk = token_list[i : i + 500]
        messages = [
            messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data={
                    "type": "chat_message",
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                },
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id="messages",
                        sound="default",
                    ),
                ),
            )
            for token in chunk
        ]
        try:
            result = messaging.send_each(messages)
            delivered += result.success_count
        except FirebaseError:
            continue
    return delivered
