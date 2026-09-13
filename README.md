# Srizon Message API Server

UI-free FastAPI backend for the Srizon Message chat system. A browser website, Android app, iOS app, Windows app, Linux app, or any other client can use the same API.

## What this server provides

- Signup/login with PBKDF2-SHA256 password hashing
- JWT bearer authentication
- `/api/v1/me` and user directory
- Fixed `Public Lounge` group; clients cannot create groups
- One-to-one direct messages between any registered users
- Persistent database storage via SQLAlchemy
- SQLite for local development and PostgreSQL for production
- Paginated message history
- Reply-to-message support
- Edit own messages
- Delete own messages with a tombstone
- Read receipts
- WebSocket realtime delivery
- Presence online/offline state
- Typing events
- Ping/pong keepalive
- Multiple WebSocket connections per user
- Device-token registration for future push notification services
- CORS configuration for browser clients
- Automatic OpenAPI/Swagger documentation
- Optional Redis pub/sub so multiple API instances can share realtime events
- No HTML/CSS/JavaScript UI is included

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
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`
- Health: `http://127.0.0.1:8000/api/v1/health`

## Production with PostgreSQL + Redis

```bash
docker compose up --build
```

For real deployment, set a strong random `JWT_SECRET` and your real `CORS_ORIGINS`.

## Client authentication

For REST requests:

```http
Authorization: Bearer <access_token>
```

For WebSocket:

```text
wss://YOUR-DOMAIN/api/v1/ws?token=<access_token>
```

## Main endpoints

### Auth

- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/login`
- `GET /api/v1/me`

### Discovery

- `GET /api/v1/users`
- `GET /api/v1/chats`

### Messages

- `GET /api/v1/history/{chat_type}/{target_id}?limit=50&before=<message_id>`
- `POST /api/v1/messages`
- `PATCH /api/v1/messages/{message_id}`
- `DELETE /api/v1/messages/{message_id}`
- `POST /api/v1/messages/read`

### Devices

- `POST /api/v1/devices/register`
- `DELETE /api/v1/devices/{device_id}`

### Realtime

- `WS /api/v1/ws?token=<access_token>`

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

Client events supported by the WebSocket endpoint:

```json
{"type":"ping"}
```

```json
{"type":"presence.request"}
```

```json
{"type":"typing.start","conversation_id":"dm_<user_a>_<user_b>"}
```

```json
{"type":"typing.stop","conversation_id":"dm_<user_a>_<user_b>"}
```

## Notes about scaling

The local ConnectionManager is process-local when Redis is not configured. For multiple API instances, set `REDIS_URL` so realtime events are distributed through Redis pub/sub. PostgreSQL should be used instead of SQLite in a multi-instance production deployment.

## Message model

A direct conversation ID is deterministic:

```text
dm_<sorted_user_id_a>_<sorted_user_id_b>
```

The fixed public room ID is configurable, defaulting to:

```text
public_lounge
```

## Security

Do not commit `.env`. Use HTTPS/WSS in production. Put the server behind a reverse proxy/load balancer, rate-limit authentication endpoints, rotate the JWT secret when needed, and keep PostgreSQL/Redis private.

## Push notifications (FCM)

Android clients register their Firebase Cloud Messaging token through:

```text
POST /api/v1/devices/register
```

When a message is created, the backend sends a push notification to the recipient devices using Firebase Cloud Messaging HTTP/2 through the Firebase Admin SDK.

Configure Firebase on the backend with:

```env
FIREBASE_SERVICE_ACCOUNT_JSON={...}
```

or:

```env
FIREBASE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
```

For Vercel, use the JSON environment variable. Do not commit service-account keys.

The Android app requests `POST_NOTIFICATIONS` on Android 13+ and creates a `messages` notification channel. Firebase's Android setup documentation requires a `FirebaseMessagingService` for custom message handling, and Android 13+ requires runtime notification permission. See the Firebase documentation for current platform requirements.
