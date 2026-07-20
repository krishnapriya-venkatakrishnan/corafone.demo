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
        # Explicit charset -- Supabase Storage serves a bare "text/plain"
        # back with no charset, and browsers/HTTP clients default an
        # unspecified text/* to ISO-8859-1, which mangles any non-ASCII
        # character (e.g. a curly apostrophe becomes "I\xe2\x80\x99m" ->
        # "Iâ€™m") on download. Uploading with the charset stated doesn't
        # by itself fix files already in the bucket -- see
        # download_call_log's explicit UTF-8 decode below, which does.
        "Content-Type": "text/plain; charset=utf-8",
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
        # Explicit decode, not `response.text` -- httpx falls back to
        # ISO-8859-1 for a `text/plain` response with no charset (which is
        # what every transcript already in the bucket has, upload fix
        # above notwithstanding), silently mangling non-ASCII bytes rather
        # than raising. This repairs transcripts already stored, not just
        # ones uploaded after the fix.
        return response.content.decode("utf-8")
