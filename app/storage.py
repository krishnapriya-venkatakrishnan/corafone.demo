"""Supabase Storage access -- uploads the per-call transcript (see
app/voice_agent.py's teardown_session). A separate REST service from
Postgres, so kept out of app/db.py."""

import logging

import httpx

from . import config

logger = logging.getLogger("corafone")

_STORAGE_BUCKET = "communications"


async def upload_call_log(path: str, content: str) -> None:
    """Uploads `content` to `{_STORAGE_BUCKET}/{path}` in Supabase Storage."""
    url = f"{config.SUPABASE_URL}/storage/v1/object/{_STORAGE_BUCKET}/{path}"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "text/plain",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, content=content.encode("utf-8"))
        response.raise_for_status()
