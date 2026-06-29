# Idempotency Gateway

A payment processing API that guarantees no request is ever processed twice, even if the client retries it multiple times. Built for FinSafe Transactions Ltd. as a solution to their double-charging problem.

---

## The Problem

When a client sends a payment request and the network times out, their system retries. If the server already processed the first one, the customer gets charged twice. This API sits in front of the payment processor and makes sure that never happens.

---

## How it works

Every payment request must include an `Idempotency-Key` header — a unique string the client generates per transaction (usually a UUID).

- **First time the key is seen** → process the payment, store the response, return it.
- **Same key comes in again with the same body** → skip processing entirely, return the stored response with an `X-Cache-Hit: true` header.
- **Same key but different body** → reject it with a 409. Someone is either making a mistake or trying something shady.
- **Two identical requests arrive at the same time** → only one gets processed. The second one waits for the first to finish and then gets its result. No race conditions, no duplicates.

Keys expire after 24 hours. After that, the same key can be used for a new transaction.

---

## Stack

- **Python + FastAPI** — async support made the locking logic straightforward
- **SQLite** — simple, zero config, good enough for this use case
- **aiosqlite** — async SQLite driver so the DB calls don't block the event loop

---

## Flow Diagram

```
Client
  │
  ├─ POST /process-payment
  │   Headers: Idempotency-Key: <uuid>
  │   Body: { "amount": 100, "currency": "GHS" }
  │
  ▼
Idempotency Check
  │
  ├─ Key not seen before?
  │     → Acquire lock
  │     → Mark as "processing" in DB
  │     → Run payment (2s simulation)
  │     → Save response
  │     → Return 201
  │
  ├─ Key exists, same body?
  │     → Return cached response
  │     → Status 200 + X-Cache-Hit: true
  │
  ├─ Key exists, different body?
  │     → Return 409 Conflict
  │
  └─ Key exists, still processing? (race condition)
        → Wait for lock to release
        → Read result from DB
        → Return 200 + X-Cache-Hit: true
```

---

## Setup

```bash
git clone https://github.com/<your-username>/idempotency-gateway.git
cd idempotency-gateway

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt

uvicorn app.main:app --reload
```

Server runs at `http://127.0.0.1:8000`. Swagger docs at `http://127.0.0.1:8000/docs`.

---

## API

### `POST /process-payment`

**Headers**
| Header | Required | Description |
|---|---|---|
| `Idempotency-Key` | Yes | Unique string per transaction |
| `Content-Type` | Yes | `application/json` |

**Body**
```json
{
  "amount": 100,
  "currency": "GHS"
}
```

**First request → 201**
```json
{
  "message": "Charged 100 GHS",
  "transaction_id": "txn_a1b2c3d4_1719432000",
  "amount": 100,
  "currency": "GHS",
  "status": "success"
}
```

**Retry with same key → 200 + `X-Cache-Hit: true`**
Same response body, no delay, no double charge.

**Same key, different body → 409**
```json
{
  "error": "Idempotency key already used for a different request body."
}
```

---

### `GET /health`
```json
{ "status": "ok" }
```

---

### `DELETE /admin/purge-expired`

Manually purge records older than 24 hours.

**Header:** `X-Admin-Key: finsafe-admin-secret`

```json
{
  "purged": 3,
  "message": "Removed 3 expired record(s)."
}
```

---

## Design decisions

**Why asyncio locks instead of a DB-level lock?**
SQLite doesn't support the kind of advisory locking needed to block concurrent requests on the same key. I used a per-key `asyncio.Lock` in memory instead. When two requests with the same key arrive simultaneously, the second one waits at the lock, then reads the result the first one wrote. It's simpler than it sounds and works reliably within a single server process.

**Why hash the body instead of storing it raw?**
Comparing two JSON objects isn't as simple as string equality — key order can differ. I serialize the body with sorted keys and hash it with SHA-256. That gives a consistent fingerprint regardless of how the client serializes its JSON.

**Why SQLite?**
It's zero config and the reviewer can run this immediately after cloning. In a real deployment this would be PostgreSQL or Redis (Redis has built-in TTL which is perfect for idempotency keys).

**The 2-second delay**
It's there to simulate real payment processing time and to make the race condition scenario actually testable. You can see it clearly when you send the first request — it pauses, then responds.

---

## Extra feature — 24-hour key expiry

Idempotency keys shouldn't live forever. After 24 hours, a client retrying an old payment should get a new transaction, not a cached response from the day before.

Every record has an `expires_at` column that's automatically set to 24 hours after creation. A background task runs every hour to clean up expired records. There's also an admin endpoint to trigger a manual purge if needed.

This is how Stripe handles it — keys are valid for 24 hours, after which they're treated as new requests.