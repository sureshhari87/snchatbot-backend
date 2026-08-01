import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str


def authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.secret_key:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        subject = str(payload.get("sub") or payload.get("user_id") or "").strip()
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if not subject:
        raise HTTPException(status_code=401, detail="Invalid access token subject")
    return AuthenticatedUser(subject=subject)


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, scope: str, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        bucket_key = (key, scope)
        with self._lock:
            events = self._events[bucket_key]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(retry_after)},
                )
            events.append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()


def ai_rate_limit(
    request: Request,
    user: AuthenticatedUser = Depends(authenticated_user),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    limiter.check(user.subject, request.url.path, settings.ai_requests_per_minute)
    return user


def concept_rate_limit(
    request: Request,
    user: AuthenticatedUser = Depends(authenticated_user),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    limiter.check(user.subject, request.url.path, settings.concept_requests_per_minute)
    return user
