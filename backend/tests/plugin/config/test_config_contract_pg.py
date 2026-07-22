"""系统参数配置的真实 PostgreSQL 契约回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.database.db import async_db_session
from backend.plugin.config.api.v1.sys.config import get_all_configs, get_config
from backend.plugin.config.crud.crud_config import config_dao
from backend.plugin.config.model.config import Config
from backend.plugin.config.schema.config import GetConfigDetail, UpdateConfigsParam

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_config_api_and_bulk_update_keep_typed_orm_contract() -> None:
    """配置查询返回 DTO，批量更新接受含主键的批量参数并保留其余字段。"""
    suffix = uuid4().hex[:8]
    config_type = f'QUALITY_{suffix}'
    key = f'QUALITY_KEY_{suffix}'

    async with async_db_session() as db:
        config = Config(
            name='质量门禁配置',
            type=config_type,
            key=key,
            value='初始值',
            is_frontend=False,
            remark='真实 PostgreSQL 回归',
        )
        db.add(config)
        await db.flush()

        all_response = await get_all_configs(db, config_type)
        detail_response = await get_config(db, config.id)

        count = await config_dao.bulk_update(
            db,
            [
                UpdateConfigsParam(
                    id=config.id,
                    name=config.name,
                    type=config.type,
                    key=config.key,
                    value='更新值',
                    is_frontend=config.is_frontend,
                    remark=config.remark,
                )
            ],
        )
        updated = await config_dao.get(db, config.id)

        assert any(item.id == config.id and isinstance(item, GetConfigDetail) for item in all_response.data)
        assert isinstance(detail_response.data, GetConfigDetail)
        assert count == 1
        assert updated is not None
        assert updated.value == '更新值'
