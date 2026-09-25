from __future__ import annotations

import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope as ASGIScope, Send

from .models import CaseEnvelope, SearchRequest
from .normalize import UnsafeCase, normalize_case, normalize_fingerprint, normalize_scope_filter
from .repository import IdempotencyConflict, PostgresAtlasRepository


logger = logging.getLogger("atlas")
MAX_BODY_BYTES = 40 * 1024
MIN_TOKEN_BYTES = 24


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > self.limit:
            await self._too_large(send)
            return

        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.extend(message.get("body", b""))
            if len(chunks) > self.limit:
                await self._too_large(send)
                return
            if not message.get("more_body", False):
                break

        body = bytes(chunks)
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def _too_large(send: Send) -> None:
        body = b'{"detail":"Request body exceeds 40 KiB"}'
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


def create_app(repository: Any | None = None, token: str | None = None) -> FastAPI:
    configured_token = token

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        actual_token = configured_token or os.environ.get("ATLAS_API_TOKEN")
        if actual_token is None or len(actual_token.encode("utf-8")) < MIN_TOKEN_BYTES:
            raise RuntimeError("ATLAS_API_TOKEN must contain at least 24 bytes")
        app.state.token = actual_token
        app.state.repository = repository
        if app.state.repository is None:
            app.state.repository = PostgresAtlasRepository()
        app.state.repository.migrate()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False

    app = FastAPI(title="FCAPSule Atlas", version="1.0.0", lifespan=lifespan)
    app.add_middleware(MaxBodySizeMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Exclude rejected input values so validation failures cannot echo credentials.
        errors = [{"loc": error.get("loc", []), "msg": error.get("msg", "Invalid value"), "type": error.get("type", "value_error")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    def require_token(request: Request) -> None:
        expected = getattr(request.app.state, "token", None)
        if not expected:
            raise HTTPException(status_code=503, detail="Atlas is not configured")
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        valid = scheme.casefold() == "bearer" and bool(supplied)
        if valid:
            valid = hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def repo(request: Request) -> Any:
        instance = getattr(request.app.state, "repository", None)
        if not getattr(request.app.state, "ready", False) or instance is None:
            raise HTTPException(status_code=503, detail="Atlas data service is not ready")
        return instance

    def call_repository(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Atlas repository request failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Atlas data service is unavailable") from None

    @app.get("/healthz", include_in_schema=False)
    def healthz(request: Request) -> JSONResponse:
        instance = getattr(request.app.state, "repository", None)
        if not getattr(request.app.state, "ready", False) or instance is None:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        try:
            instance.healthcheck()
        except Exception as exc:
            logger.error("Atlas healthcheck failed (%s)", type(exc).__name__)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ok"})

    @app.post("/v1/cases", status_code=201, dependencies=[Depends(require_token)])
    def create_case(envelope: CaseEnvelope, request: Request) -> JSONResponse:
        try:
            case = normalize_case(envelope)
        except UnsafeCase as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        result = call_repository(repo(request).create_case, case)
        return JSONResponse(status_code=201 if result["created"] else 200, content=result)

    @app.get("/v1/cases/{case_id}", dependencies=[Depends(require_token)])
    def get_case(case_id: str, request: Request) -> dict[str, Any]:
        result = call_repository(repo(request).get_case, case_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Case not found")
        return {"case": result}

    @app.post("/v1/search", dependencies=[Depends(require_token)])
    def search(body: SearchRequest, request: Request) -> dict[str, Any]:
        if body.before and body.observed_before and body.before != body.observed_before:
            raise HTTPException(status_code=422, detail="before and observed_before must match when both are supplied")
        try:
            scope_filter = normalize_scope_filter(body.scope.model_dump(exclude_none=True) if body.scope else None)
            fingerprint = normalize_fingerprint(body.fingerprint)
        except UnsafeCase as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return call_repository(
            repo(request).search,
            scope=scope_filter,
            query=body.query,
            fingerprint=fingerprint,
            instance_id=body.instance_id,
            observed_after=body.observed_after,
            before=body.observed_before or body.before,
            limit=body.limit,
        )

    @app.get("/v1/patterns", dependencies=[Depends(require_token)])
    def list_patterns(
        request: Request,
        scope: str | None = Query(default=None),
        query: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=10, ge=1, le=50),
        before: datetime | None = Query(default=None),
        observed_before: datetime | None = Query(default=None),
        environment: str | None = Query(default=None, max_length=80),
        cluster: str | None = Query(default=None, max_length=200),
        namespace: str | None = Query(default=None, max_length=200),
        service: str | None = Query(default=None, max_length=200),
        workload: str | None = Query(default=None, max_length=200),
        cnfc_id: str | None = Query(default=None, max_length=200),
        vnfc_id: str | None = Query(default=None, max_length=200),
    ) -> dict[str, Any]:
        if before and observed_before and before != observed_before:
            raise HTTPException(status_code=422, detail="before and observed_before must match when both are supplied")
        cutoff = observed_before or before
        if cutoff is not None and cutoff.tzinfo is None:
            raise HTTPException(status_code=422, detail="timestamps must include a timezone")
        scope_values: dict[str, Any] = {}
        if scope:
            try:
                parsed_scope = json.loads(scope)
            except json.JSONDecodeError:
                raise HTTPException(status_code=422, detail="scope must be a JSON object") from None
            if not isinstance(parsed_scope, dict):
                raise HTTPException(status_code=422, detail="scope must be a JSON object")
            scope_values.update(parsed_scope)
        for key, value in {
            "environment": environment, "cluster": cluster, "namespace": namespace,
            "service": service, "workload": workload, "cnfc_id": cnfc_id, "vnfc_id": vnfc_id,
        }.items():
            if value is not None:
                scope_values[key] = value
        allowed_scope_keys = {"environment", "cluster", "namespace", "service", "workload", "cnfc_id", "vnfc_id"}
        if set(scope_values) - allowed_scope_keys or any(not isinstance(value, str) for value in scope_values.values()):
            raise HTTPException(status_code=422, detail="scope contains an unknown field or non-string value")
        try:
            scope_filter = normalize_scope_filter(scope_values)
        except UnsafeCase as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return call_repository(
            repo(request).list_patterns,
            scope=scope_filter,
            query=query,
            before=cutoff,
            limit=limit,
        )

    @app.get("/v1/patterns/{pattern_id}", dependencies=[Depends(require_token)])
    def get_pattern(pattern_id: str, request: Request) -> dict[str, Any]:
        result = call_repository(repo(request).get_pattern, pattern_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Pattern not found")
        return result

    return app


app = create_app()
