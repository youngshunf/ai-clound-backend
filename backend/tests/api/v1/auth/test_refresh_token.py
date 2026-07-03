from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from starlette.responses import Response

from backend.app.admin.service import auth_service as auth_service_module
from backend.app.admin.service.auth_service import AuthService
from backend.common.exception import errors

pytestmark = pytest.mark.asyncio


class _MissingUserDao:
    async def get(self, db: Any, user_id: int) -> None:
        return None


class _Request:
    cookies: dict[str, str] = {}


async def test_refresh_token_for_deleted_user_requires_relogin(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理用户数据后，旧 refresh_token 续期应提示重新登录，而不是暴露 404 用户不存在."""
    monkeypatch.setattr(
        auth_service_module,
        'jwt_decode',
        lambda token: SimpleNamespace(id=123, session_uuid='old-session'),
    )
    monkeypatch.setattr(auth_service_module, 'user_dao', _MissingUserDao())

    with pytest.raises(errors.TokenError) as exc_info:
        await AuthService.refresh_token(
            db=object(),  # type: ignore[arg-type]
            request=_Request(),  # type: ignore[arg-type]
            response=Response(),
            body_refresh_token='deleted-user-refresh-token',
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == 'Refresh Token 已过期，请重新登录'
