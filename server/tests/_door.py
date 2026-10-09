"""A fake front door for the tests: a Starlette app with FastAPI-style
decorators, since the server no longer depends on FastAPI (W-1019).

A handler takes `request` if it names it, the path's values by name, and any
other parameter from the query string, converted to its annotated type. A
dict it returns goes out as JSON."""
from __future__ import annotations

import inspect

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


# Annotations arrive as strings under `from __future__ import annotations`.
_TYPES = {"int": int, "float": float, "str": str, int: int, float: float, str: str}


class Door(Starlette):
    def api_route(self, path: str, methods: list[str]):
        def add(fn):
            params = inspect.signature(fn).parameters

            async def endpoint(request: Request):
                kwargs = {}
                for name, p in params.items():
                    if name == "request":
                        kwargs[name] = request
                    elif name in request.path_params:
                        kwargs[name] = request.path_params[name]
                    elif name in request.query_params:
                        conv = _TYPES.get(p.annotation, str)
                        kwargs[name] = conv(request.query_params[name])
                out = await fn(**kwargs)
                return JSONResponse(out) if isinstance(out, (dict, list)) else out

            self.router.routes.append(Route(path, endpoint, methods=methods))
            return fn
        return add

    def get(self, path: str):
        return self.api_route(path, ["GET"])

    def post(self, path: str):
        return self.api_route(path, ["POST"])
