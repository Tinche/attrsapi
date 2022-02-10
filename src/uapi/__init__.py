from typing import Any, Literal, Union

from attrs import Factory, define, frozen
from cattrs import Converter, GenConverter
from incant import Incanter

from .cookies import Cookie

__all__ = ["Cookie", "make_base_incanter", "BaseApp"]


@frozen
class Header:
    name: str


Parameter = Union[Header]


def make_base_incanter() -> Incanter:
    """Create the base (non-framework) incanter."""
    res = Incanter()
    return res


@define
class BaseApp:
    framework_incant: Incanter
    converter: Converter = Factory(GenConverter)
    base_incant: Incanter = Factory(make_base_incanter)

    def serve_swaggerui(self):
        from .swaggerui import swaggerui

        async def swaggerui_handler() -> tuple[str, Literal[200], dict]:
            return swaggerui, 200, {"content-type": "text/html"}

        self.route("/swaggerui")(swaggerui_handler)

    def serve_redoc(self):
        from .swaggerui import redoc

        async def redoc_handler() -> tuple[str, Literal[200], dict]:
            return redoc, 200, {"content-type": "text/html"}

        self.route("/redoc")(redoc_handler)


def redirect(
    res: tuple[Any, Any, dict[str, str]], location: str
) -> tuple[None, Literal[303], dict[str, str]]:
    return None, 303, res[2] | {"Location": location}
