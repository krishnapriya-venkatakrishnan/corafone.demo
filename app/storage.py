"""Supabase Storage access -- uploads and fetches the per-call transcript
(see app/voice_agent.py's teardown_session and app/dashboard_api.py). A
separate REST service from Postgres, so kept out of app/db.py."""

import logging

import httpx

from . import config

logger = logging.getLogger("corafone")

_STORAGE_BUCKET = "communications"


async def upload_call_log(path: str, content: str) -> None:
    """Uploads `content` to `{_STORAGE_BUCKET}/{path}` in Supabase Storage.
    Upserts -- live call paths are always unique (account_id + timestamp) so
    this never overwrites a real transcript, but it lets seed scripts
    (app/database/upload_seed_transcripts.py) be re-run safely."""
    url = f"{config.SUPABASE_URL}/storage/v1/object/{_STORAGE_BUCKET}/{path}"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "text/plain",
        "x-upsert": "true",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, content=content.encode("utf-8"))
        response.raise_for_status()


async def download_call_log(path: str) -> str:
    """Fetches the transcript at `{_STORAGE_BUCKET}/{path}` (see
    voice_session_metrics.transcript_path) as text."""
    url = f"{config.SUPABASE_URL}/storage/v1/object/{_STORAGE_BUCKET}/{path}"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text
