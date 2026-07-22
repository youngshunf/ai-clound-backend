"""代码生成业务元数据的真实 PostgreSQL 回归测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.database.db import async_db_session
from backend.plugin.code_generator.api.v1.business import get_all_businesses, get_business
from backend.plugin.code_generator.crud.crud_business import gen_business_dao
from backend.plugin.code_generator.model.business import GenBusiness
from backend.plugin.code_generator.schema.business import CreateGenBusinessParam, GetGenBusinessDetail

pytestmark = pytest.mark.asyncio(loop_scope='module')


def test_codegen_identifiers_accept_digits_after_the_initial_letter() -> None:
    """代码生成器必须接受 Python 与 PostgreSQL 都合法的 `s3_storage` 类标识符。"""
    created_time = datetime.now(UTC)

    create_param = CreateGenBusinessParam(
        app_name='app2',
        table_name='s3_storage',
        doc_comment='对象存储',
    )
    detail = GetGenBusinessDetail.model_validate({
        **create_param.model_dump(),
        'id': 1,
        'created_time': created_time,
        'updated_time': None,
    })

    assert detail.app_name == 'app2'
    assert detail.table_name == 's3_storage'


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


async def test_business_api_returns_declared_detail_dto() -> None:
    """代码生成 API 必须把 ORM 实体转换为其声明的详情 DTO。"""
    table_name = f'codegen_api_{uuid4().hex}'

    async with async_db_session() as db:
        business = GenBusiness(
            app_name='hasn',
            table_name=table_name,
            doc_comment='接口 DTO 回归',
        )
        db.add(business)
        await db.flush()

        all_response = await get_all_businesses(db)
        detail_response = await get_business(db, business.id)

        matching = [item for item in all_response.data if item.id == business.id]
        assert len(matching) == 1
        assert isinstance(matching[0], GetGenBusinessDetail)
        assert isinstance(detail_response.data, GetGenBusinessDetail)
        assert detail_response.data.table_name == table_name
