import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.database import init_db
from app.idempotency import process_idempotent_payment, purge_expired_keys


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


async def _cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        purged = await purge_expired_keys()
        if purged:
            print(f"[cleanup] Purged {purged} expired idempotency record(s).")


app = FastAPI(
    title="FinSafe Idempotency Gateway",
    description="A payment processing API with a full idempotency layer.",
    version="1.0.0",
    lifespan=lifespan,
)


class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, example=100)
    currency: str = Field(..., min_length=3, max_length=3, example="GHS")


@app.get("/", tags=["Health"])
async def root():
    return {"service": "FinSafe Idempotency Gateway", "status": "running"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}


@app.post("/process-payment", status_code=201, tags=["Payments"])
async def process_payment(
    body: PaymentRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="A unique UUID per transaction attempt.",
    ),
):
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required.")

    payload = body.model_dump()
    response_body, status_code, cache_hit = await process_idempotent_payment(
        idempotency_key, payload
    )

    headers = {}
    if cache_hit:
        headers["X-Cache-Hit"] = "true"
        status_code = 200

    return JSONResponse(content=response_body, status_code=status_code, headers=headers)


@app.delete("/admin/purge-expired", tags=["Admin"])
async def purge_expired(x_admin_key: str = Header(..., alias="X-Admin-Key")):
    if x_admin_key != "finsafe-admin-secret":
        raise HTTPException(status_code=403, detail="Invalid admin key.")
    purged = await purge_expired_keys()
    return {"purged": purged, "message": f"Removed {purged} expired record(s)."}