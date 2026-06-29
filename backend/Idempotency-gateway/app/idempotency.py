import asyncio
import hashlib
import json
import time

from app.database import (
    delete_expired,
    get_record,
    insert_in_flight,
    insert_record,
    update_record,
)

_locks: dict[str, asyncio.Lock] = {}
_lock_registry_lock = asyncio.Lock()


def hash_body(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def get_or_create_lock(key: str) -> asyncio.Lock:
    async with _lock_registry_lock:
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        return _locks[key]


async def release_lock(key: str):
    async with _lock_registry_lock:
        _locks.pop(key, None)


async def process_idempotent_payment(
    idempotency_key: str,
    payload: dict,
) -> tuple[dict, int, bool]:
    body_hash = hash_body(payload)
    lock = await get_or_create_lock(idempotency_key)

    async with lock:
        record = await get_record(idempotency_key)

        if record and record["status"] == "completed":
            if record["body_hash"] == body_hash:
                return json.loads(record["response"]), record["status_code"], True
            else:
                return (
                    {
                        "error": "Idempotency key already used for a different request body."
                    },
                    409,
                    False,
                )

        await insert_in_flight(idempotency_key, body_hash)

        try:
            await asyncio.sleep(2)

            amount = payload.get("amount", 0)
            currency = payload.get("currency", "USD").upper()

            response_body = {
                "message": f"Charged {amount} {currency}",
                "transaction_id": f"txn_{idempotency_key[:8]}_{int(time.time())}",
                "amount": amount,
                "currency": currency,
                "status": "success",
            }
            status_code = 201

            await update_record(idempotency_key, status_code, response_body)
            return response_body, status_code, False

        except Exception:
            await update_record(
                idempotency_key,
                500,
                {"error": "Internal processing error. Please retry."},
            )
            raise

        finally:
            await release_lock(idempotency_key)


async def purge_expired_keys() -> int:
    return await delete_expired()