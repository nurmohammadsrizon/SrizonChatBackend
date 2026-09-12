# Client integration examples

This backend has no UI. Clients are responsible for rendering messages and handling keyboard/mobile UX.

## JavaScript / React / Web

REST:

```js
const res = await fetch(`${API}/api/v1/messages`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify({
    chat_type: "personal",
    target_id: otherUserId,
    text: "Hello",
  }),
});
```

WebSocket:

```js
const ws = new WebSocket(`${WS_API}/api/v1/ws?token=${encodeURIComponent(token)}`);
ws.onmessage = event => console.log(JSON.parse(event.data));
ws.send(JSON.stringify({ type: "ping" }));
```

## Python client

```python
import requests

base = "https://your-domain.example"
r = requests.post(f"{base}/api/v1/auth/login", json={
    "username": "alice",
    "password": "your-password",
})
token = r.json()["access_token"]

me = requests.get(
    f"{base}/api/v1/me",
    headers={"Authorization": f"Bearer {token}"},
)
print(me.json())
```

For mobile apps, use the same HTTPS API and WSS endpoint. Android/iOS do not need to know anything about the server's UI because no UI exists in this project.
