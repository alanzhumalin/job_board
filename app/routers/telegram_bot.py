import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from app.config import get_settings
from app.telegram_service import process_update

router = APIRouter(prefix="/telegram", tags=["telegram"])
logger = logging.getLogger(__name__)
settings = get_settings()


@router.post("/webhook/{secret}")
async def telegram_webhook(secret: str, update: dict):
    if not settings.telegram_webhook_secret or secret != settings.telegram_webhook_secret:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    asyncio.create_task(process_update(update))
    return {"ok": True}
