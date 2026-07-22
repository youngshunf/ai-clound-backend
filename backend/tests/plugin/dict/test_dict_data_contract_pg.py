"""字典数据 API 的真实 PostgreSQL 契约回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.database.db import async_db_session
from backend.plugin.dict.api.v1.sys.dict_data import (
    get_all_dict_datas,
    get_dict_data,
    get_dict_data_by_type_code,
)
from backend.plugin.dict.api.v1.sys.dict_type import get_all_dict_types, get_dict_type
from backend.plugin.dict.model.dict_data import DictData
from backend.plugin.dict.model.dict_type import DictType
from backend.plugin.dict.schema.dict_data import GetDictDataDetail
from backend.plugin.dict.schema.dict_type import GetDictTypeDetail

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_dict_data_api_returns_declared_detail_dto() -> None:
    """字典查询接口必须把真实 ORM 实体转换为详情 DTO。"""
    suffix = uuid4().hex[:8]
    type_code = f'quality_{suffix}'

    async with async_db_session() as db:
        dict_type = DictType(name='质量门禁', code=type_code, remark='真实 PostgreSQL 回归')
        db.add(dict_type)
        await db.flush()

        dict_data = DictData(
            type_id=dict_type.id,
            type_code=type_code,
            label='通过',
            value='passed',
            color='green',
            sort=1,
            status=1,
            remark='类型契约验证',
        )
        db.add(dict_data)
        await db.flush()

        all_response = await get_all_dict_datas(db)
        detail_response = await get_dict_data(db, dict_data.id)
        typed_response = await get_dict_data_by_type_code(db, type_code)
        all_types_response = await get_all_dict_types(db)
        type_response = await get_dict_type(db, dict_type.id)

        assert any(item.id == dict_data.id and isinstance(item, GetDictDataDetail) for item in all_response.data)
        assert isinstance(detail_response.data, GetDictDataDetail)
        assert typed_response.data[0].id == dict_data.id
        assert any(item.id == dict_type.id and isinstance(item, GetDictTypeDetail) for item in all_types_response.data)
        assert isinstance(type_response.data, GetDictTypeDetail)
