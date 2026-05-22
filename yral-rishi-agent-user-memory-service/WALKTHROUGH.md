# WALKTHROUGH.md — trace a chat turn through user-memory-service

## The user action: Rishi sends "Hello Tara" on his Motorola

This walkthrough follows the data flow for a single chat turn through the user-memory-service. At the end of this walkthrough, the message and the AI's reply are both saved to Postgres.

*Note: The full RPC route handlers are in Deliverable 2 (not yet live). This walkthrough describes the intended flow once Deliverable 2 ships.*

---

### Step 1 — Mobile sends the message

Rishi's YRAL app sends:
```
POST /api/v1/chat/conversations/{conversation_id}/messages
Authorization: Bearer <jwt>
{"content": "Hello Tara", "client_message_id": "abc123"}
```
to the **public-api** service.

---

### Step 2 — public-api calls the orchestrator

public-api forwards the request to the **orchestrator** (`/v1/turn`), which:
1. Calls this service's `GET /v1/conversations/{id}/messages` to fetch the last N messages as LLM context
2. Calls the LLM (Gemini) with the soul file + context + "Hello Tara"
3. Gets the AI's reply

---

### Step 3 — orchestrator saves the turn (Deliverable 2)

After getting the AI's reply, the orchestrator calls this service's `POST /v1/conversations/{id}/messages` with both messages:
```json
{
  "messages": [
    {"role": "user", "content": "Hello Tara", "client_message_id": "abc123"},
    {"role": "assistant", "content": "Hi! How are you?", "gemini_metadata": {...}}
  ]
}
```

---

### Step 4 — user-memory-service persists to Postgres

This service's `POST /v1/conversations/{id}/messages` handler:
1. Acquires a connection from the asyncpg pool (`app/database.get_pool()`)
2. Inserts the user message row into `messages`
3. Inserts the assistant message row into `messages`
4. Updates `conversations.last_message_at = NOW()` and `message_count += 2`
5. Returns both message rows

---

### Step 5 — Mobile sees the reply

public-api wraps the assistant message in the `ApiResponse[MessageResponse]` envelope and returns it to the mobile app. Rishi's phone shows "Hi! How are you?" in the chat bubble.

---

## The inbox flow (Deliverable 2)

When Rishi opens the YRAL app and sees his chat list:

1. mobile calls `GET /api/v2/chat/conversations`
2. public-api calls this service's `GET /v1/conversations/by-user/{user_id}`
3. This service does:
   ```sql
   SELECT * FROM conversations
   WHERE user_id = $1 AND soft_deleted_at IS NULL
   ORDER BY last_message_at DESC LIMIT 20;
   ```
   — using `conversations_by_user_active_idx` (the partial index for speed)
4. Returns the list to public-api
5. public-api wraps in `ApiResponse[list[ConversationResponse]]` and returns to mobile

---

## Key files in this walkthrough

| Step | File |
|---|---|
| DB connection pool | `app/database.py` |
| Schema (tables) | `app/migrations/versions/001_initial_schema.py` |
| HTTP routes (Deliverable 2) | `app/api/conversation_routes.py` *(not yet created)* |
| Config (DB URL, etc.) | `app/config.py` |
| Service entry point | `app/main.py` |
