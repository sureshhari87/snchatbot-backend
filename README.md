---
title: Jewellery Chat API
emoji:  💎
colorFrom: pink
colorTo: purple
sdk: docker
pinned: false
---
# Jewellery Chat API

Backend API for a Flutter jewellery ecommerce assistant.

## Endpoints

- `GET /`
- `GET /health`
- `POST /chat`

## Example POST /chat
```json
{
  "message": "show me gold rings under 20000",
  "user_id": "user_1",
  "session_id": "session_1"
}
```
