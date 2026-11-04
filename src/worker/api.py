"""Authenticated HTTP routes exposed by the Worker process."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import os
from pathlib import Path
import secrets
import tempfile
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from worker.dispatch import (
    IDENTIFIER,
    MAX_COMPRESSED_BYTES,
    VerificationDispatch,
    decode_header,
    validate_run_dispatch,
    validate_verification_dispatch,
)
from worker.errors import ApiError

if TYPE_CHECKING:
    from worker.main import WorkerProcess


def _error_response(error: ApiError) -> JSONResponse:
    """Return the Worker API error shape."""
    return JSONResponse(
        status_code=error.status_code,
        content={'code': error.code, 'error': error.message},
    )


def _identity(evaluation_id: str, attempt: int) -> dict[str, object]:
    return {'evaluation_id': evaluation_id, 'attempt': attempt}


def _query_identity(request: Request) -> tuple[str, int]:
    """Validate the evaluation_id and attempt query parameters."""
    evaluation_id = request.query_params.get('evaluation_id')
    if evaluation_id is None or IDENTIFIER.fullmatch(evaluation_id) is None:
        raise ApiError(400, 'bad_request', 'evaluation_id is invalid')
    attempt_text = request.query_params.get('attempt')
    if attempt_text is None or not attempt_text.isdecimal() or int(attempt_text) < 1:
        raise ApiError(400, 'bad_request', 'attempt must be a positive integer')
    return evaluation_id, int(attempt_text)


def _control_dispatch(request: Request) -> VerificationDispatch:
    """Decode the identity header shared by the control endpoints."""
    metadata = decode_header(request.headers.get('worker-evaluation'), 'Worker-Evaluation')
    return validate_verification_dispatch(metadata)


async def _discard_body(request: Request) -> None:
    async for _ in request.stream():
        pass


async def _has_body(request: Request) -> bool:
    async for chunk in request.stream():
        if chunk:
            return True
    return False


async def _save_body(request: Request, directory: Path) -> Path | None:
    """Stream the request body into a temporary archive, or return None when it is empty."""
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix='.worker-body-', suffix='.tar.gz', dir=directory)
    path = Path(filename)
    size = 0
    keep = False
    try:
        with os.fdopen(descriptor, 'wb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_COMPRESSED_BYTES:
                    raise ApiError(413, 'payload_too_large', 'archive exceeds 1 GiB')
                target.write(chunk)
        if size == 0:
            return None
        keep = True
        return path
    finally:
        if not keep:
            path.unlink(missing_ok=True)


def register_routes(app: FastAPI, process: WorkerProcess) -> None:
    """
    Attach the authenticated run, verification, and control endpoints.

    Args:
        `app` (FastAPI): HTTPS application owned by the main process.
        `process` (WorkerProcess): Current process state and configuration.
    """
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
        return _error_response(error)

    @app.middleware('http')
    async def authenticate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        expected = f'Bearer {process.configuration.device_token}'
        if not secrets.compare_digest(request.headers.get('authorization', ''), expected):
            return _error_response(ApiError(401, 'unauthorized', 'device token is missing or invalid'))
        return await call_next(request)

    @app.get('/status')
    async def get_status() -> dict[str, object]:
        process.note_heartbeat()
        return process.status_response()

    @app.post('/run')
    async def post_run(request: Request) -> Response:
        metadata = decode_header(request.headers.get('worker-evaluation'), 'Worker-Evaluation')
        model_access = decode_header(request.headers.get('worker-model-access'), 'Worker-Model-Access')
        dispatch = validate_run_dispatch(metadata, model_access)
        process.require_dispatch_ready()
        existing = process.find_attempt(dispatch.evaluation_id, dispatch.attempt)
        if existing is not None:
            await _discard_body(request)
            return JSONResponse(status_code=200, content=_identity(dispatch.evaluation_id, dispatch.attempt))
        if dispatch.source_attempt is None:
            package = await _save_body(request, process.configuration.input_directory)
            if package is None:
                raise ApiError(415, 'unsupported_media_type', 'the first dispatch must carry a gzip tar')
            try:
                record, created = await process.accept_run(dispatch, package)
            finally:
                package.unlink(missing_ok=True)
        else:
            if await _has_body(request):
                raise ApiError(400, 'bad_request', 'a rerun request must have an empty body')
            record, created = await process.accept_run(dispatch, None)
        status_code = 202 if created else 200
        return JSONResponse(status_code=status_code, content=_identity(record.evaluation_id, record.attempt))

    @app.get('/run')
    async def get_run(request: Request) -> dict[str, object]:
        evaluation_id, attempt = _query_identity(request)
        return process.run_response(evaluation_id, attempt)

    @app.get('/run/log')
    async def get_run_log(request: Request) -> dict[str, object]:
        evaluation_id, attempt = _query_identity(request)
        return await asyncio.to_thread(process.run_log_response, evaluation_id, attempt)

    @app.get('/run/output')
    async def get_run_output(request: Request) -> Response:
        evaluation_id, attempt = _query_identity(request)
        archive = await asyncio.to_thread(process.output_archive, evaluation_id, attempt)
        return FileResponse(
            archive,
            media_type='application/gzip',
            background=BackgroundTask(archive.unlink, missing_ok=True),
        )

    @app.get('/run/output/file')
    async def get_run_output_file(request: Request) -> Response:
        evaluation_id, attempt = _query_identity(request)
        relative = request.query_params.get('path')
        if relative is None:
            raise ApiError(400, 'bad_request', 'path is required')
        target = await asyncio.to_thread(process.output_file_path, evaluation_id, attempt, relative)
        return FileResponse(target, media_type='application/octet-stream')

    @app.post('/verification')
    async def post_verification(request: Request) -> Response:
        metadata = decode_header(request.headers.get('worker-evaluation'), 'Worker-Evaluation')
        dispatch = validate_verification_dispatch(metadata)
        process.require_dispatch_ready()
        package = await _save_body(request, process.configuration.input_directory)
        try:
            record = await process.accept_verification(dispatch, package)
        finally:
            if package is not None:
                package.unlink(missing_ok=True)
        return JSONResponse(status_code=202, content=_identity(record.evaluation_id, record.attempt))

    @app.get('/verification')
    async def get_verification(request: Request) -> dict[str, object]:
        evaluation_id, attempt = _query_identity(request)
        return process.verification_response(evaluation_id, attempt)

    @app.get('/verification/score')
    async def get_verification_score(request: Request) -> Response:
        evaluation_id, attempt = _query_identity(request)
        score = await asyncio.to_thread(process.verification_score_path, evaluation_id, attempt)
        return FileResponse(score, media_type='application/json')

    @app.post('/pause')
    async def pause(request: Request) -> Response:
        await process.pause_attempt(_control_dispatch(request))
        return Response(status_code=202)

    @app.post('/resume')
    async def resume(request: Request) -> Response:
        await process.resume_attempt(_control_dispatch(request))
        return Response(status_code=202)

    @app.post('/cancel')
    async def cancel(request: Request) -> Response:
        await process.cancel_attempt(_control_dispatch(request))
        return Response(status_code=202)

    @app.post('/delete')
    async def delete(request: Request) -> Response:
        await process.delete_attempt(_control_dispatch(request))
        return Response(status_code=204)
