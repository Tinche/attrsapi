from asyncio import CancelledError, create_task, sleep
from datetime import timedelta
from typing import Callable, Literal

import pytest
from aioredis import Redis
from httpx import AsyncClient

from tests.aiohttp import run_on_aiohttp
from uapi.aiohttp import App as AiohttpApp
from uapi.cookies import CookieSettings
from uapi.sessions.redis import AsyncSession, configure_async_sessions


def configure_redis_session_app(app: AiohttpApp):
    configure_async_sessions(
        app,
        Redis.from_url("redis://"),
        cookie_settings=CookieSettings(secure=False),
        max_age=timedelta(seconds=1),
    )

    @app.get("/")
    async def index(session: AsyncSession) -> str:
        if "user_id" not in session:
            return "naughty!"
        else:
            return session["user_id"]

    @app.post("/login")
    async def login(
        username: str, session: AsyncSession
    ) -> tuple[None, Literal[201], dict]:
        session["user_id"] = username
        return await session.update_session(None, 201, namespace=username)

    @app.post("/logout")
    async def logout(session: AsyncSession) -> tuple[None, Literal[204], dict]:
        return await session.clear_session(None, 204)


@pytest.fixture(scope="session")
async def redis_session_app(unused_tcp_port_factory: Callable[..., int]):
    unused_tcp_port = unused_tcp_port_factory()
    app = AiohttpApp()
    configure_redis_session_app(app)
    t = create_task(run_on_aiohttp(app, unused_tcp_port))
    yield unused_tcp_port
    t.cancel()
    try:
        await t
    except CancelledError:
        pass


async def test_login_logout(redis_session_app: int):
    """Test path parameter handling."""
    username = "MyCoolUsername"
    async with AsyncClient() as client:
        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "naughty!"

        resp = await client.post(
            f"http://localhost:{redis_session_app}/login",
            params={"username": username},
        )

        assert resp.status_code == 201

        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "MyCoolUsername"

        async with AsyncClient() as new_client:
            resp = await new_client.get(f"http://localhost:{redis_session_app}/")
            assert resp.text == "naughty!"

        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "MyCoolUsername"

        resp = await client.post(f"http://localhost:{redis_session_app}/logout")
        assert resp.text == ""
        assert resp.status_code == 204
        assert not resp.cookies

        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "naughty!"


async def test_session_expiry(redis_session_app: int):
    """Test path parameter handling."""
    username = "MyCoolUsername"
    async with AsyncClient() as client:
        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "naughty!"

        resp = await client.post(
            f"http://localhost:{redis_session_app}/login",
            params={"username": username},
        )

        assert resp.status_code == 201

        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "MyCoolUsername"

        await sleep(2)

        resp = await client.get(f"http://localhost:{redis_session_app}/")
        assert resp.text == "naughty!"
