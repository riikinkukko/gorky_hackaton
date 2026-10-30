from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.api.session import COOKIE, verify_cookie
from app.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_user_id(
    request: Request,
    x_user_id: Annotated[int | None, Header(ge=1, le=2_147_483_647)] = None,
) -> int:
    """Кто пользователь (SEC-01).

    prod: только подписанная cookie сессии (POST /api/session), иначе 401.
    Вне prod (и в переходном режиме ALLOW_HEADER_AUTH): cookie, затем X-User-Id, затем 1.
    """
    c: Container = request.app.state.container
    found = verify_cookie(c.settings, request.cookies.get(COOKIE))
    if found is not None:
        return found[0]
    if c.settings.app_env == "prod" and not c.settings.allow_header_auth:
        raise HTTPException(status_code=401, detail="Нужен вход: POST /api/session")
    return x_user_id or 1


def get_cookie_session(request: Request) -> str | None:
    """session_id из подписанной cookie — ключ лимитов (из тела не годится, SEC-03)."""
    c: Container = request.app.state.container
    found = verify_cookie(c.settings, request.cookies.get(COOKIE))
    return found[1] if found else None


ContainerDep = Annotated[Container, Depends(get_container)]
CookieSessionDep = Annotated[str | None, Depends(get_cookie_session)]
UserIdDep = Annotated[int, Depends(get_user_id)]
