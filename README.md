# Srizon Message API — JSON Edition

UI-free FastAPI backend for the Srizon Message chat system. This version intentionally uses **JSON files instead of SQLite/PostgreSQL/Redis** because the project is meant for personal/small-scale use, not a large company.

## Storage

All persistent application data lives in:

```text
data/
├── users.json
├── conversations.json
├── messages.json
├── message_reads.json
└── devices.json
```

Writes use a temporary file + atomic replace, and the in-process async lock prevents concurrent writes inside one server process.

## Features

- Signup/login with PBKDF2-SHA256 password hashing
- JWT bearer authentication
- User directory
- Fixed `Public Lounge`; users cannot create groups
- One-to-one direct messages
- JSON persistence
- Paginated message history
- Reply support
- Edit/delete own messages
- Read receipts
- WebSocket realtime delivery
- Presence online/offline state
- Typing events
- Multiple connections per user in one server process
- FCM device-token registration and push notifications
- CORS
- Swagger/OpenAPI
- No UI included

## Run locally

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

- Swagger: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/api/v1/health`

## JSON data backup

The simplest backup is:

```bash
cp -r data data-backup
```

For recovery, stop the server and restore the JSON files into `data/`.

## FCM

Push notification is optional. Register devices through:

```text
POST /api/v1/devices/register
```

Configure either:

```env
FIREBASE_SERVICE_ACCOUNT_JSON={...}
```

or:

```env
FIREBASE_SERVICE_ACCOUNT_FILE=./firebase-service-account.json
```

Do not commit Firebase credentials.

## WebSocket

```text
ws://127.0.0.1:8000/api/v1/ws?token=<access_token>
```

For HTTPS production:

```text
wss://YOUR-DOMAIN/api/v1/ws?token=<access_token>
```

Supported client events:

```json
{"type":"ping"}
```

```json
{"type":"presence.request"}
```

```json
{"type":"typing.start","conversation_id":"dm_<a>_<b>"}
```

```json
{"type":"typing.stop","conversation_id":"dm_<a>_<b>"}
```

Typical server events:

- `connection.ready`
- `message.created`
- `message.updated`
- `message.deleted`
- `message.read`
- `presence.changed`
- `presence.snapshot`
- `typing.start`
- `typing.stop`

## Important limitation

JSON storage is deliberately simple and appropriate for a personal/friend-circle deployment. It is **not** designed for high write concurrency, many server instances, or large-scale production traffic. Keep the server as a single application instance and back up the `data/` directory regularly.
# SrizonChatBackend
