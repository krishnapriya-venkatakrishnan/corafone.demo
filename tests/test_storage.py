"""app/storage.py: request shape for both the upload and download sides of
Supabase Storage access -- httpx is mocked, no real network call."""

from unittest.mock import AsyncMock, MagicMock, patch

from app import storage


async def test_upload_call_log_posts_content():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.storage.httpx.AsyncClient", return_value=mock_client):
        await storage.upload_call_log("42/20260101T000000Z/log.txt", "hello world")

    url, kwargs = mock_client.post.call_args.args, mock_client.post.call_args.kwargs
    assert "42/20260101T000000Z/log.txt" in url[0]
    assert kwargs["content"] == b"hello world"


async def test_download_call_log_returns_text():
    """G3: decodes `.content` as UTF-8 explicitly, not `.text` -- httpx
    falls back to ISO-8859-1 for a `text/plain` response with no charset
    (what every transcript in the bucket has), silently mangling non-ASCII
    bytes. The explicit decode repairs transcripts already stored, not
    just ones uploaded after the Content-Type fix."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = "hello world ’s".encode("utf-8")
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.storage.httpx.AsyncClient", return_value=mock_client):
        content = await storage.download_call_log("42/20260101T000000Z/log.txt")

    assert content == "hello world ’s"
    url = mock_client.get.call_args.args[0]
    assert "42/20260101T000000Z/log.txt" in url


async def test_upload_call_log_sends_utf8_charset():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.storage.httpx.AsyncClient", return_value=mock_client):
        await storage.upload_call_log("42/20260101T000000Z/log.txt", "hello world")

    headers = mock_client.post.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "text/plain; charset=utf-8"
