from inspect import Parameter, signature
from typing import Any, Callable, Union, cast

from attr import has
from cattr import structure, unstructure
from flask.app import Flask
from quart import Quart
from quart import Response as QuartResponse
from quart import request

from . import Header
from .flask import make_openapi_spec as flask_openapi_spec
from .path import parse_angle_path_params
from .responses import returns_status_code
from .types import is_subclass

try:
    from functools import partial

    from ujson import dumps as usjon_dumps

    dumps: Callable[[Any], Union[bytes, str]] = partial(
        usjon_dumps, ensure_ascii=False, escape_forward_slashes=False
    )
except ImportError:
    from json import dumps


def _generate_wrapper(
    handler: Callable,
    path: str,
    body_dumper=lambda v: dumps(unstructure(v)),
    path_loader=structure,
    query_loader=structure,
):
    sig = signature(handler)
    params_meta: dict[str, Header] = getattr(handler, "__attrs_api_meta__", {})
    path_params = parse_angle_path_params(path)
    lines = []
    post_lines = []
    lines.append(f"async def handler({', '.join(path_params)}) -> __attrsapi_Response:")

    globs = {
        "__attrsapi_inner": handler,
        "__attrsapi_request": request,
        "__attrsapi_Response": QuartResponse,
    }

    res_is_native = False
    if (ret_type := sig.return_annotation) in (None, Parameter.empty):
        lines.append("  __attrsapi_sc = 200")
        lines.append("  await __attrsapi_inner(")
        post_lines.append("  return __attrsapi_Response('', status=__attrsapi_sc)")
    else:
        if returns_status_code(ret_type):
            lines.append("  __attrsapi_sc, __attrsapi_res = await __attrsapi_inner(")
            post_lines.append(
                "  return __attrsapi_Response(response=dumper(__attrsapi_res), status=__attrsapi_sc)"
            )
            globs["dumper"] = body_dumper
        else:
            res_is_native = ret_type is not Parameter.empty and is_subclass(
                ret_type, QuartResponse
            )

            if res_is_native:
                # The response is native.
                lines.append("  return await __attrsapi_inner(")
            else:
                lines.append(
                    "  return Response(response=dumper(await __attrsapi_inner("
                )
                post_lines.append("  )")
                globs["dumper"] = body_dumper

    for arg, arg_param in sig.parameters.items():
        if arg in path_params:
            arg_annotation = sig.parameters[arg].annotation
            if arg_annotation in (Parameter.empty, str):
                lines.append(f"    {arg},")
            else:
                lines.append(
                    f"    __attrsapi_path_loader({arg}, __attrsapi_{arg}_type),"
                )
                globs["__attrsapi_path_loader"] = path_loader
                globs[f"__attrsapi_{arg}_type"] = arg_annotation
        elif arg_meta := params_meta.get(arg):
            if isinstance(arg_meta, Header):
                # A header param.
                lines.append(f"    request.headers['{arg_meta.name}'],")
        elif (arg_type := arg_param.annotation) is not Parameter.empty and has(
            arg_type
        ):
            # defaulting to body
            pass
        else:
            # defaulting to query
            if arg_param.default is Parameter.empty:
                expr = f"__attrsapi_request.args['{arg}']"
            else:
                expr = f"__attrsapi_request.args.get('{arg}', __attrsapi_{arg}_default)"
                globs[f"__attrsapi_{arg}_default"] = arg_param.default

            if (
                arg_param.annotation is not str
                and arg_param.annotation is not Parameter.empty
            ):
                expr = f"__attrsapi_query_loader({expr}, __attrsapi_{arg}_type)"
                globs["__attrsapi_query_loader"] = query_loader
                globs[f"__attrsapi_{arg}_type"] = arg_param.annotation
            lines.append(f"    {expr},")

    lines.append("  )")

    ls = "\n".join(lines + post_lines)
    eval(compile(ls, "", "exec"), globs)

    fn = globs["handler"]

    return fn


def route(path: str, app: Quart, methods=["GET"]) -> Callable[[Callable], Callable]:
    def inner(handler: Callable) -> Callable:
        adapted = _generate_wrapper(handler, path)
        adapted.__attrsapi_handler__ = handler
        app.route(path, methods=methods, endpoint=handler.__name__)(adapted)
        return handler

    return inner


def make_openapi_spec(app: Quart, title: str = "Server", version: str = "1.0"):
    return flask_openapi_spec(
        cast(Flask, app), title, version, native_response_cl=QuartResponse
    )
