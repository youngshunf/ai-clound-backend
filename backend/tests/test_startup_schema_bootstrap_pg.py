"""启动期 schema 自举硬化真实 PG 验收（零 mock·防「部署后一重启即崩」老坑复发）。

背景：fba 启动 lifespan 跑 `create_tables()` → `metadata.create_all`——`create_all` 只建表、
不建 schema。新应用模块若声明独立 PG schema（ADR-15 一应用一 schema）而生产库从没建过，
`create_all` 一撞缺失 schema 就抛 `InvalidSchemaNameError` → ASGI lifespan startup failed →
worker 崩溃重启死循环（已多次复发：external_mcp / hasn_design / hasn_reel / hasn_stock…）。

硬化：`create_tables()` 建表前先从 metadata 汇总所有自定义 schema、逐个
`CREATE SCHEMA IF NOT EXISTS`。本测试用真库证明：
1. `metadata_schemas()` 纯函数：去重升序、剔除 public/None、全非空字符串；
2. 端到端自举：往权威 metadata 注册一张落在**全新 schema** 的探针表（模拟「新增独立
   schema 的应用模块」），`create_tables()` 应先建 schema 再建表、全程不炸——旧代码此处会
   直接 `InvalidSchemaNameError`；
3. 幂等：反复 `create_tables()` / `CREATE SCHEMA IF NOT EXISTS` 均不报错。

需本地 PostgreSQL :15432（与其余 *_pg 测试同库）。
"""

from __future__ import annotations

import pytest

from sqlalchemy import BigInteger, Column, Table, text
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.common.model import MappedBase
from backend.database.db import (
    _should_auto_create_tables,
    async_engine,
    create_tables,
    metadata_schemas,
)

# 注意：不用模块级 pytestmark——本文件混有同步纯函数测试；async 测试各自显式标注。
# 每个 async 测试收尾 dispose 共享 async_engine：pytest-asyncio 默认 function-scoped 事件循环，
# 不 dispose 会把上一测试循环里的池化连接带进下一测试的新循环 → RuntimeError: Event loop is closed。

# 全新的探针 schema——库里本不存在，用于验证「缺失 schema 自动建」路径
_PROBE_SCHEMA = 'hasn_startup_probe'
_PROBE_TABLE = 'startup_probe'


def test_metadata_schemas_shape() -> None:
    """纯函数不变量：去重升序、无 public/None、全是非空字符串。"""
    schemas = metadata_schemas()
    assert schemas == sorted(set(schemas)), 'metadata_schemas 应去重且升序'
    assert 'public' not in schemas, 'public 不应作为自定义 schema 返回'
    assert None not in schemas
    assert all(isinstance(s, str) and s for s in schemas), '每个 schema 都应是非空字符串'


@pytest.mark.parametrize(
    ('environment', 'auto_flag', 'expected'),
    [
        # 生产恒 False——硬闸凌驾 auto_flag（即便被误设 True 也不生效），杜绝启动期误建旧表
        ('prod', True, False),
        ('prod', False, False),
        # 非生产按 auto_flag 决定：默认 True 自动建表，显式关则不建
        ('dev', True, True),
        ('dev', False, False),
        ('test', True, True),
    ],
)
def test_should_auto_create_tables(environment: str, auto_flag: bool, expected: bool) -> None:
    """R1-11 纯判定：生产恒关启动期 create_all，非生产随 auto_flag。"""
    assert _should_auto_create_tables(environment, auto_flag) is expected


@pytest.mark.asyncio
async def test_create_tables_bootstraps_missing_schema() -> None:
    """端到端：metadata 里出现全新 schema 时，create_tables 先建 schema 再建表、全程不炸。"""
    # 先确保干净起点（幂等清理，防上次残留）
    async with async_engine.begin() as conn:
        await conn.execute(DropSchema(_PROBE_SCHEMA, if_exists=True, cascade=True))

    # 往权威 metadata 注册一张落在全新 schema 的探针表（模拟「新增独立 schema 的应用模块」）
    probe = Table(
        _PROBE_TABLE,
        MappedBase.metadata,
        Column('id', BigInteger, primary_key=True),
        schema=_PROBE_SCHEMA,
    )
    try:
        # 枚举确实抓到了这个全新 schema（把纯函数与真实路径绑定）
        assert _PROBE_SCHEMA in metadata_schemas()

        # 关键：此刻库里没有该 schema。旧 create_all 会抛 InvalidSchemaNameError；
        # 硬化后应先 CREATE SCHEMA IF NOT EXISTS 再建表，不报错。
        await create_tables()

        async with async_engine.connect() as conn:
            got_schema = (
                await conn.execute(
                    text('SELECT 1 FROM information_schema.schemata WHERE schema_name = :n'),
                    {'n': _PROBE_SCHEMA},
                )
            ).scalar()
            got_table = (
                await conn.execute(
                    text('SELECT 1 FROM information_schema.tables WHERE table_schema = :s AND table_name = :t'),
                    {'s': _PROBE_SCHEMA, 't': _PROBE_TABLE},
                )
            ).scalar()
        assert got_schema == 1, '硬化后应自动建出缺失 schema'
        assert got_table == 1, 'schema 建好后 create_all 应把表也建出来'

        # 幂等：再跑一次不炸（schema/表都已存在）
        await create_tables()
    finally:
        # 复原：从 metadata 摘除探针表 + 删库里的探针 schema，避免污染其它测试/开发库
        MappedBase.metadata.remove(probe)
        async with async_engine.begin() as conn:
            await conn.execute(DropSchema(_PROBE_SCHEMA, if_exists=True, cascade=True))
        # 释放本测试循环里的池化连接，避免带入下一测试的新事件循环
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_create_schema_if_not_exists_is_idempotent() -> None:
    """CREATE SCHEMA IF NOT EXISTS 对已存在/不存在的 schema 反复执行都不炸。"""
    async with async_engine.begin() as conn:
        await conn.execute(DropSchema(_PROBE_SCHEMA, if_exists=True, cascade=True))
    try:
        # 连建两次都应成功（IF NOT EXISTS 幂等）
        for _ in range(2):
            async with async_engine.begin() as conn:
                await conn.execute(CreateSchema(_PROBE_SCHEMA, if_not_exists=True))
        async with async_engine.connect() as conn:
            exists = (
                await conn.execute(
                    text('SELECT 1 FROM information_schema.schemata WHERE schema_name = :n'),
                    {'n': _PROBE_SCHEMA},
                )
            ).scalar()
        assert exists == 1
    finally:
        async with async_engine.begin() as conn:
            await conn.execute(DropSchema(_PROBE_SCHEMA, if_exists=True, cascade=True))
        await async_engine.dispose()
