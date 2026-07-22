"""API Key 管理接口的真实 PostgreSQL 响应契约回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.newapi.apikey.api import admin_create_api_key
from backend.app.newapi.apikey.schema import AdminCreateUserApiKeyParam, CreateUserApiKeyResponse
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_admin_create_api_key_returns_declared_response_dto() -> None:
    """管理员创建参数的管理字段不能泄漏到创建 DTO。"""
    user_id = 900_000_000 + int(uuid4().hex[:8], 16) % 90_000_000
    async with async_db_session() as db:
        response = await admin_create_api_key(
            db,
            AdminCreateUserApiKeyParam(name=f'质量门禁-{uuid4().hex[:8]}', user_id=user_id),
        )

        assert isinstance(response.data, CreateUserApiKeyResponse)
        assert response.data.name.startswith('质量门禁-')
