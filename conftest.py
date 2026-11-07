"""Automated tests must never consume a real provider quota or fetch remote data."""
import httpx
import pytest


@pytest.fixture(autouse=True)
def block_real_http_transports(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must use an explicit mock/ASGI transport; real HTTP is disabled")

    async def blocked_async(*args, **kwargs):
        blocked()

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
