"""代码生成业务元数据的真实 PostgreSQL 回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.database.db import async_db_session
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.model.business import GenBusiness

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_update_app_name_preserves_other_business_metadata() -> None:
    """仅更新应用名时，既有表元数据不得被完整更新模型的默认值覆盖。"""
    table_name = f'codegen_quality_{uuid4().hex}'

    async with async_db_session() as db:
        business = GenBusiness(
            app_name='hasn',
            table_name=table_name,
            doc_comment='类型收敛回归',
            table_comment='原始表注释',
            class_name='Quality',
            schema_name='QualitySchema',
            filename='quality',
            datetime_mixin=False,
            api_version='v2',
            tag='质量门禁',
            api_scope='agent',
            gen_path='backend/app/hasn',
            remark='不得被应用名更新覆盖',
        )
        db.add(business)
        await db.flush()

        count = await gen_business_dao.update_app_name(db, business.id, 'hasn_quality')
        updated = await gen_business_dao.get(db, business.id)

        assert count == 1
        assert updated is not None
        assert updated.app_name == 'hasn_quality'
        assert updated.doc_comment == '类型收敛回归'
        assert updated.api_version == 'v2'
        assert updated.api_scope == 'agent'
        assert updated.remark == '不得被应用名更新覆盖'
