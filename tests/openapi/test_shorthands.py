"""OpenAPI works with shorthands."""
from datetime import datetime, timezone
from typing import Any

from uapi.openapi import MediaType, OneOfSchema, Response, Schema
from uapi.quart import App
from uapi.shorthands import ResponseShorthand
from uapi.status import BaseResponse, Ok

from ..test_shorthands import DatetimeShorthand


def test_no_openapi() -> None:
    """Shorthands without OpenAPI support work."""
    app = App()

    @app.get("/")
    async def datetime_handler() -> datetime:
        return datetime(2000, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)

    app.add_response_shorthand(DatetimeShorthand)

    spec = app.make_openapi_spec()

    assert spec.paths["/"]
    assert spec.paths["/"].get is not None
    assert spec.paths["/"].get.responses == {}


def test_has_openapi() -> None:
    """Shorthands without OpenAPI support work."""

    class OpenAPIDateTime(DatetimeShorthand):
        @staticmethod
        def make_openapi_response() -> Response | None:
            return Response(
                "DESC",
                {"test": MediaType(Schema(Schema.Type.STRING, format="datetime"))},
            )

    app = App()

    @app.get("/")
    async def datetime_handler() -> datetime:
        return datetime(2000, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)

    app.add_response_shorthand(OpenAPIDateTime)

    spec = app.make_openapi_spec()

    assert spec.paths["/"]
    assert spec.paths["/"].get is not None
    assert spec.paths["/"].get.responses == {
        "200": Response(
            "DESC", {"test": MediaType(Schema(Schema.Type.STRING, format="datetime"))}
        )
    }


def test_unions() -> None:
    """A union of a shorthand and a BaseResponse works."""
    app = App()

    @app.get("/")
    async def index() -> Ok[str] | bytes:
        return b""

    spec = app.make_openapi_spec()

    op = spec.paths["/"].get
    assert op is not None
    assert op.responses == {
        "200": Response(
            "OK",
            content={
                "text/plain": MediaType(Schema(Schema.Type.STRING)),
                "application/octet-stream": MediaType(
                    Schema(Schema.Type.STRING, format="binary")
                ),
            },
        )
    }


def test_unions_same_content_type() -> None:
    """Content types coalesce."""

    class MyStr:
        pass

    class CustomShorthand(ResponseShorthand[MyStr]):
        @staticmethod
        def response_adapter(value: Any) -> BaseResponse:
            return Ok(value)

        @staticmethod
        def is_union_member(value: Any) -> bool:
            return isinstance(value, str)

        @staticmethod
        def make_openapi_response() -> Response | None:
            return Response(
                "OK", {"text/plain": MediaType(Schema(Schema.Type.BOOLEAN))}
            )

    app = App().add_response_shorthand(CustomShorthand)

    @app.get("/")
    async def index() -> str | MyStr:
        return ""

    spec = app.make_openapi_spec()

    op = spec.paths["/"].get
    assert op is not None
    assert op.responses == {
        "200": Response(
            "OK",
            content={
                "text/plain": MediaType(
                    OneOfSchema(
                        [Schema(Schema.Type.STRING), Schema(Schema.Type.BOOLEAN)]
                    )
                )
            },
        )
    }


def test_unions_of_shorthands() -> None:
    """A union of shorthands works."""
    app = App()

    @app.get("/")
    async def index() -> str | None | bytes:
        return b""

    spec = app.make_openapi_spec()

    op = spec.paths["/"].get
    assert op is not None
    assert op.responses == {
        "200": Response(
            "OK",
            content={
                "text/plain": MediaType(Schema(Schema.Type.STRING)),
                "application/octet-stream": MediaType(
                    Schema(Schema.Type.STRING, format="binary")
                ),
            },
        ),
        "204": Response("No content"),
    }
