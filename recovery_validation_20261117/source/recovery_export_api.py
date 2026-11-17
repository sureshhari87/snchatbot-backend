"""Opt-in maintenance route; the ordinary main application does not install it."""

import asyncio
import logging
import os
import stat
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from runtime_recovery import (
    CHALLENGE_PATTERN,
    MAX_ENVELOPE_BYTES,
    MAX_WINDOW_SECONDS,
    SOURCE_PATTERN,
    WINDOW_PATTERN,
    decode_json,
    encode_json,
    recipient_fingerprint,
    validate_envelope,
    worker_command,
)

EXPORT_PATH = "/admin/recovery/export"
logger = logging.getLogger("snchatbot.recovery")
BODY_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class ExportWindow:
    public_pem: bytes
    recipient: str
    source: str
    window_id: str
    expires_at: float

    def validate(self):
        try:
            if not 0 < self.expires_at - time.time() <= MAX_WINDOW_SECONDS:
                raise ValueError()
            if not SOURCE_PATTERN.fullmatch(self.source) or not WINDOW_PATTERN.fullmatch(
                self.window_id
            ):
                raise ValueError()
            if recipient_fingerprint(self.public_pem) != self.recipient:
                raise ValueError()
        except Exception:
            raise ValueError("Recovery configuration is incomplete or invalid") from None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]):
        if environment.get("RECOVERY_EXPORT_ENABLED") != "1":
            return None
        try:
            pem = environment["RECOVERY_EXPORT_PUBLIC_KEY"].encode()
            recipient = environment["RECOVERY_EXPORT_RECIPIENT_SHA256"]
            source = environment["RECOVERY_EXPORT_SOURCE_ID"]
            window_id = environment["RECOVERY_EXPORT_WINDOW_ID"]
            expires = datetime.fromisoformat(
                environment["RECOVERY_EXPORT_EXPIRES_AT"].replace("Z", "+00:00")
            )
            if (
                expires.tzinfo is None
                or not 0 < expires.timestamp() - time.time() <= MAX_WINDOW_SECONDS
            ):
                raise ValueError()
            if not SOURCE_PATTERN.fullmatch(source) or not WINDOW_PATTERN.fullmatch(window_id):
                raise ValueError()
            if recipient_fingerprint(pem) != recipient:
                raise ValueError()
            return cls(pem, recipient, source, window_id, expires.timestamp())
        except Exception:
            raise ValueError("Recovery configuration is incomplete or invalid") from None


def _claim_window(root: Path, window_id: str):
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except FileExistsError:
        raise OSError("Invalid claim directory") from None
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise OSError("Invalid claim directory")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise OSError("Claim directory permissions are not private")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / (window_id + ".used"), flags, 0o600)
    os.close(descriptor)


def _run_worker(window: ExportWindow, challenge: str) -> bytes:
    control = encode_json(
        {
            "public_key": window.public_pem.decode(),
            "recipient": window.recipient,
            "source": window.source,
            "window": window.window_id,
            "challenge": challenge,
        }
    )
    # Fixed interpreter/bootstrap; arguments and stdin contain public metadata only.
    result = subprocess.run(  # nosec B603
        worker_command(),
        input=control,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
        shell=False,
    )
    if result.returncode or len(result.stdout) > MAX_ENVELOPE_BYTES:
        result.stdout = b""
        raise RuntimeError("Recovery worker unavailable")
    try:
        validate_envelope(
            result.stdout,
            recipient=window.recipient,
            source=window.source,
            window=window.window_id,
            challenge=challenge,
        )
    except Exception:
        result.stdout = b""
        raise RuntimeError("Recovery worker returned invalid output") from None
    return result.stdout


def _export_once(window: ExportWindow, challenge: str, claim_root: Path):
    if time.time() >= window.expires_at:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        _claim_window(claim_root, window.window_id)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Recovery window already used") from None
    except OSError:
        raise HTTPException(status_code=503, detail="Recovery export unavailable") from None
    try:
        result = _run_worker(window, challenge)
        if time.time() >= window.expires_at:
            raise RuntimeError("Recovery window expired")
        return result
    except Exception:
        raise HTTPException(status_code=503, detail="Recovery export unavailable") from None


def install(app, authorize: Callable, *, audit=None, window=None, claim_root=None) -> bool:
    def emit(event, request=None, admin=None, *, level=logging.INFO, **fields):
        fields.update(
            user_id=getattr(admin, "id", None), window_id=getattr(window, "window_id", None)
        )
        if audit is not None:
            audit(event, request, level=level, **fields)
        else:
            logger.log(
                level,
                encode_json(
                    {
                        "event": event,
                        "level": logging.getLevelName(level).lower(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "request_id": getattr(getattr(request, "state", None), "request_id", None),
                        **fields,
                    }
                ).decode(),
            )

    try:
        window = window or ExportWindow.from_environment(os.environ)
        if window is not None:
            if not isinstance(window, ExportWindow):
                raise ValueError()
            window.validate()
    except ValueError:
        window = None
        emit("recovery.disabled_invalid_configuration", level=logging.WARNING)
        return False
    if window is None:
        return False
    claim_root = claim_root or Path(tempfile.gettempdir()) / "sona-runtime-recovery"

    @app.post(EXPORT_PATH, include_in_schema=False)
    async def export_runtime_settings(request: Request, admin=Depends(authorize)):
        if time.time() >= window.expires_at:
            raise HTTPException(status_code=404, detail="Not found")
        if getattr(admin, "is_admin", False) is not True:
            raise HTTPException(status_code=403, detail="Admin access required")
        if request.url.scheme != "https":
            raise HTTPException(status_code=400, detail="HTTPS required")
        if "origin" in request.headers:
            raise HTTPException(status_code=403, detail="Browser requests are not accepted")
        if (
            request.url.query
            or request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise HTTPException(status_code=400, detail="Invalid recovery request")
        raw = await _read_body(request)
        try:
            body = decode_json(raw, 256)
            if not isinstance(body, dict) or set(body) != {"challenge"}:
                raise ValueError()
            challenge = body["challenge"]
            if not isinstance(challenge, str) or not CHALLENGE_PATTERN.fullmatch(challenge):
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid recovery request") from None
        try:
            result = await run_in_threadpool(_export_once, window, challenge, claim_root)
        except HTTPException as error:
            emit(
                "recovery.export_failed",
                request,
                admin,
                level=logging.WARNING,
                status_code=error.status_code,
            )
            raise
        emit("recovery.export_created", request, admin)
        return Response(
            result,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'attachment; filename="runtime-settings.encrypted.json"',
            },
        )

    return True


async def _read_body(request):
    async def consume():
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 256:
                raise HTTPException(status_code=413, detail="Recovery request too large")
        return bytes(raw)

    try:
        return await asyncio.wait_for(consume(), timeout=BODY_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Recovery request timed out") from None
