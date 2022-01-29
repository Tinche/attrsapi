from collections import defaultdict
from inspect import Parameter as Parameter
from inspect import Signature, getfullargspec, signature
from typing import Any, Callable, Optional, Tuple, Union

from aiohttp.web import Request as FrameworkRequest
from aiohttp.web import Response as FrameworkResponse
from aiohttp.web import RouteDef, RouteTableDef
from aiohttp.web_app import Application
from aiohttp.web_urldispatcher import (
    DynamicResource,
    Handler,
    PlainResource,
    ResourceRoute,
    RoutesView,
)
from attrs import Factory, define
from cattr._compat import get_args, get_origin, has, is_union_type
from cattrs import Converter
from incant import Hook, Incanter

from attrsapi.requests import get_cookie_name

from . import BaseApp, Header
from .openapi import PYTHON_PRIMITIVES_TO_OPENAPI, MediaType, OpenAPI
from .openapi import Parameter as OpenApiParameter
from .openapi import Reference, Response, Schema, build_attrs_schema
from .path import parse_curly_path_params
from .responses import empty_dict, get_status_code_results
from .types import is_subclass


def make_cookie_dependency(cookie_name: str, default=Signature.empty):
    if default is Signature.empty:

        def read_cookie(request: FrameworkRequest):
            return request.cookies[cookie_name]

    else:

        def read_cookie(request: FrameworkRequest):
            return request.cookies.get(cookie_name, default)

    return read_cookie


def make_aiohttp_incanter(converter: Converter) -> Incanter:
    """Create the framework incanter for Aiohttp."""
    res = Incanter()

    def query_factory(p: Parameter):
        def read_query(request: FrameworkRequest):
            return converter.structure(
                request.query[p.name]
                if p.default is Signature.empty
                else request.query.get(p.name, p.default),
                p.annotation,
            )

        return read_query

    res.register_hook_factory(
        lambda p: p.annotation is not FrameworkRequest,
        query_factory,
    )

    def string_query_factory(p: Parameter):
        def read_query(request: FrameworkRequest):
            return (
                request.query[p.name]
                if p.default is Signature.empty
                else request.query.get(p.name, p.default)
            )

        return read_query

    res.register_hook_factory(
        lambda p: p.annotation in (Signature.empty, str), string_query_factory
    )
    res.register_hook_factory(
        lambda p: get_cookie_name(p.annotation, p.name) is not None,
        lambda p: make_cookie_dependency(get_cookie_name(p.annotation, p.name), default=p.default),  # type: ignore
    )
    return res


def return_adapter(return_type: Any) -> Optional[Callable[..., Tuple]]:
    """The aiohttp return adapter."""
    if return_type in (Signature.empty, FrameworkResponse):
        # You're on your own, buddy.
        return None
    if return_type in (str, bytes):
        return lambda r: (r, 200, empty_dict)
    if return_type is None:
        return lambda r: (None, 200, empty_dict)
    if get_origin(return_type) is tuple and len(get_args(return_type)) == 2:
        return lambda r: (r[0], r[1], empty_dict)
    if is_union_type(return_type):
        return (
            lambda r: r
            if len(r) == 3
            else ((r[0], r[1], empty_dict) if len(r) == 2 else (r[0], 200, empty_dict))
        )
    return None


def framework_return_adapter(val: Tuple[Any, int, dict]):
    return FrameworkResponse(body=val[0] or b"", status=val[1], headers=val[2])


@define
class App(BaseApp):
    framework_incant: Incanter = Factory(
        lambda self: make_aiohttp_incanter(self.converter), takes_self=True
    )

    def route(self, path: str, routes: RouteTableDef, methods=["GET"]):
        def wrapper(handler: Callable) -> Callable:
            ra = return_adapter(signature(handler).return_annotation)
            path_params = parse_curly_path_params(path)
            hooks = [Hook.for_name(p, None) for p in path_params]
            if ra is None:
                base_handler = self.base_incant.prepare(handler, is_async=True)
                prepared = self.framework_incant.prepare(
                    base_handler, hooks, is_async=True
                )
                sig = signature(prepared)
                path_types = {p: sig.parameters[p].annotation for p in path_params}

                async def adapted(
                    request: FrameworkRequest, _incant=self.framework_incant.aincant
                ) -> FrameworkResponse:
                    path_args = {
                        p: (
                            self.converter.structure(request.match_info[p], path_type)
                            if (path_type := path_types[p])
                            not in (str, Signature.empty)
                            else request.match_info[p]
                        )
                        for p in path_params
                    }
                    return await _incant(prepared, request=request, **path_args)

            else:
                base_handler = self.base_incant.prepare(handler, is_async=True)
                prepared = self.framework_incant.prepare(
                    base_handler, hooks, is_async=True
                )
                sig = signature(prepared)
                path_types = {p: sig.parameters[p].annotation for p in path_params}

                async def adapted(
                    request: FrameworkRequest,
                    _incant=self.framework_incant.aincant,
                    _ra=ra,
                    _fra=framework_return_adapter,
                ) -> FrameworkResponse:
                    path_args = {
                        p: (
                            self.converter.structure(request.match_info[p], path_type)
                            if (path_type := path_types[p])
                            not in (str, Signature.empty)
                            else request.match_info[p]
                        )
                        for p in path_params
                    }
                    return _fra(
                        _ra(await _incant(prepared, request=request, **path_args))
                    )

            adapted.__attrsapi_handler__ = base_handler  # type: ignore

            for method in methods:
                routes.route(method, path)(adapted)  # type: ignore
            return adapted

        return wrapper


def gather_endpoint_components(
    endpoint: RouteDef, components: dict[type, str]
) -> dict[type, str]:
    original_handler = getattr(endpoint.handler, "__attrsapi_handler__", None)
    if original_handler is None:
        return {}
    fullargspec = getfullargspec(original_handler)
    for arg_name in fullargspec.args:
        if arg_name in fullargspec.annotations:
            arg_type = fullargspec.annotations[arg_name]
            if has(arg_type) and arg_type not in components:
                name = arg_type.__name__
                counter = 0
                while name in components.values():
                    name = f"{arg_type.__name__}{counter}"
                    counter += 1
                components[arg_type] = name
    if ret_type := fullargspec.annotations.get("return"):
        if has(ret_type) and ret_type not in components:
            name = ret_type.__name__
            counter = 0
            while name in components.values():
                name = f"{ret_type.__name__}{counter}"
                counter += 1
            components[ret_type] = name
    return components


def build_operation(
    handler: Handler, path: str, components: dict[type, str]
) -> OpenAPI.PathItem.Operation:
    request_body = {}
    responses = {"200": Response(description="OK")}
    ct = "application/json"
    params = []
    if original_handler := getattr(handler, "__attrsapi_handler__", None):
        sig = signature(original_handler)
        path_params = parse_curly_path_params(path)
        meta_params: dict[str, Header] = getattr(
            original_handler, "__attrs_api_meta__", {}
        )
        for path_param in path_params:
            if path_param not in sig.parameters:
                raise Exception(f"Path parameter {path_param} not found")
            t = sig.parameters[path_param].annotation
            params.append(
                OpenApiParameter(
                    path_param,
                    OpenApiParameter.Kind.PATH,
                    True,
                    PYTHON_PRIMITIVES_TO_OPENAPI.get(t),
                )
            )

        for arg, arg_param in sig.parameters.items():
            if arg in path_params:
                continue
            elif arg_meta := meta_params.get(arg):
                if isinstance(arg_meta, Header):
                    params.append(
                        OpenApiParameter(
                            arg_meta.name,
                            OpenApiParameter.Kind.HEADER,
                            arg_param.default is Parameter.empty,
                            PYTHON_PRIMITIVES_TO_OPENAPI[str],
                        )
                    )
            else:
                arg_type = arg_param.annotation
                if cookie_name := get_cookie_name(arg_type, arg):
                    params.append(
                        OpenApiParameter(
                            cookie_name,
                            OpenApiParameter.Kind.COOKIE,
                            arg_param.default is Parameter.empty,
                            PYTHON_PRIMITIVES_TO_OPENAPI.get(
                                arg_param.annotation,
                                PYTHON_PRIMITIVES_TO_OPENAPI[str],
                            ),
                        )
                    )
                elif arg_type is not Parameter.empty and has(arg_type):
                    request_body["content"] = {
                        ct: MediaType(
                            Reference(f"#/components/schemas/{components[arg_type]}")
                        )
                    }
                else:
                    params.append(
                        OpenApiParameter(
                            arg,
                            OpenApiParameter.Kind.QUERY,
                            arg_param.default is Parameter.empty,
                            PYTHON_PRIMITIVES_TO_OPENAPI.get(
                                arg_param.annotation,
                                PYTHON_PRIMITIVES_TO_OPENAPI[str],
                            ),
                        )
                    )

        ret_type = sig.return_annotation
        if ret_type is Parameter.empty:
            ret_type = None
        if not is_subclass(ret_type, FrameworkResponse):
            statuses = get_status_code_results(ret_type)
            responses = {}
            for status_code, result_type in statuses:
                if result_type is str:
                    ct = "text/plain"
                    responses[str(status_code)] = Response(
                        "OK",
                        {ct: MediaType(PYTHON_PRIMITIVES_TO_OPENAPI[result_type])},
                    )
                elif result_type is None:
                    responses[str(status_code)] = Response("OK")
                elif has(result_type):
                    responses[str(status_code)] = Response(
                        "OK",
                        {
                            ct: MediaType(
                                Reference(
                                    f"#/components/schemas/{components[result_type]}"
                                )
                            )
                        },
                    )
                else:
                    responses[str(status_code)] = Response(
                        "OK",
                        {ct: MediaType(PYTHON_PRIMITIVES_TO_OPENAPI[result_type])},
                    )
    return OpenAPI.PathItem.Operation(responses, params)


def build_pathitem(
    path: str, path_routes: dict[str, Handler], components
) -> OpenAPI.PathItem:
    get = post = put = None
    if get_route := path_routes.get("get"):
        get = build_operation(get_route, path, components)
    if post_route := path_routes.get("post"):
        post = build_operation(post_route, path, components)
    if put_route := path_routes.get("put"):
        put = build_operation(put_route, path, components)
    return OpenAPI.PathItem(get=get, post=post, put=put)


def routes_to_paths(
    routes: RoutesView, components: dict[type, dict[str, OpenAPI.PathItem]]
) -> dict[str, OpenAPI.PathItem]:
    res: dict[str, dict[str, Handler]] = defaultdict(dict)

    for route_def in routes:
        if isinstance(route_def, ResourceRoute):
            resource = route_def.resource
            if resource is not None:
                if isinstance(resource, (PlainResource, DynamicResource)):
                    path = resource.canonical
                    res[path] = res[path] | {
                        route_def.method.lower(): route_def.handler
                    }

    return {k: build_pathitem(k, v, components) for k, v in res.items()}


def components_to_openapi(routes: RoutesView) -> tuple[OpenAPI.Components, dict]:
    res: dict[str, Union[Schema, Reference]] = {}

    # First pass, we build the component registry.
    components: dict[type, str] = {}
    for route_def in routes:
        if isinstance(route_def, RouteDef):
            gather_endpoint_components(route_def, components)

    for component in components:
        res[component.__name__] = build_attrs_schema(component)

    return OpenAPI.Components(res), components


def make_openapi_spec(
    app: Application, title: str = "Server", version: str = "1.0"
) -> OpenAPI:
    routes = app.router.routes()
    c, components = components_to_openapi(routes)
    return OpenAPI(
        "3.0.3", OpenAPI.Info(title, version), routes_to_paths(routes, components), c
    )
