import sys

from collections.abc import AsyncGenerator
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.common.enums import DataBaseType
from backend.common.log import log
from backend.core.conf import settings


def create_database_url(*, unittest: bool = False, with_database: bool = True) -> URL:
    """
    创建数据库链接

    :param unittest: 是否用于单元测试
    :param with_database: 是否包含数据库名（创建数据库时不需要）
    :return:
    """
    if with_database:
        database = settings.DATABASE_SCHEMA if not unittest else f'{settings.DATABASE_SCHEMA}_test'
    else:
        database = None if DataBaseType.mysql == settings.DATABASE_TYPE else 'postgres'

    url = URL.create(
        drivername='mysql+asyncmy' if DataBaseType.mysql == settings.DATABASE_TYPE else 'postgresql+asyncpg',
        username=settings.DATABASE_USER,
        password=settings.DATABASE_PASSWORD,
        host=settings.DATABASE_HOST,
        port=settings.DATABASE_PORT,
        database=database,
    )
    if DataBaseType.mysql == settings.DATABASE_TYPE and with_database:
        url = url.update_query_dict({'charset': settings.DATABASE_CHARSET})
    return url


def create_database_async_engine(url: str | URL) -> AsyncEngine:
    """
    创建数据库异步引擎

    :param url: 数据库连接地址
    :return:
    """
    try:
        return create_async_engine(
            url,
            echo=settings.DATABASE_ECHO,
            echo_pool=settings.DATABASE_POOL_ECHO,
            future=True,
            # 中等并发
            pool_size=10,  # 低：- 高：+
            max_overflow=20,  # 低：- 高：+
            pool_timeout=30,  # 低：+ 高：-
            pool_recycle=3600,  # 低：+ 高：-
            pool_pre_ping=True,  # 低：False 高：True
            pool_use_lifo=False,  # 低：False 高：True
        )
    except Exception as e:
        log.error(f'数据库连接失败 {e}')
        sys.exit()


def create_database_async_session(engine: AsyncEngine) -> async_sessionmaker[AsyncSession | Any]:
    """
    创建数据库异步会话

    :param engine: 数据库异步引擎
    :return:
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,  # 禁用自动刷新
        expire_on_commit=False,  # 禁用提交时过期
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话"""
    async with async_db_session() as session:
        yield session


async def get_db_transaction() -> AsyncGenerator[AsyncSession, None]:
    """获取带有事务的数据库会话"""
    async with async_db_session.begin() as session:
        yield session


def metadata_schemas() -> list[str]:
    """汇总 metadata 里声明的全部自定义 schema（去重升序；public/None 不算）。

    抽成纯函数便于单测：只依赖已 import 的模型 metadata，不碰数据库。
    """
    from backend.common.model import MappedBase

    return sorted({table.schema for table in MappedBase.metadata.tables.values() if table.schema})


async def create_tables() -> None:
    """创建数据库表

    ⚠️ 建表前先建齐 metadata 里声明的全部自定义 schema——`create_all` 只建表、不建 schema。
    新应用模块若声明独立 PG schema（ADR-15 一应用一 schema）而生产库从没建过，启动时
    `create_all` 一撞缺失 schema 就抛 `InvalidSchemaNameError` → ASGI lifespan startup
    failed → worker 崩溃重启死循环（「部署后一重启即崩」老坑，已多次复发：external_mcp /
    hasn_design / hasn_reel / hasn_stock…）。这里先幂等 `CREATE SCHEMA IF NOT EXISTS` 一遍，
    从根上消灭这一整类事故，让「部署新 schema 后无需人工先建 schema」。
    """
    from sqlalchemy.schema import CreateSchema

    from backend.common.model import MappedBase

    async with async_engine.begin() as coon:
        # 仅 PostgreSQL 需要显式建 schema；MySQL 无独立 schema 概念，跳过
        if DataBaseType.mysql != settings.DATABASE_TYPE:
            for schema in metadata_schemas():
                await coon.execute(CreateSchema(schema, if_not_exists=True))
        await coon.run_sync(MappedBase.metadata.create_all)


async def drop_tables() -> None:
    """丢弃数据库表"""
    from backend.common.model import MappedBase

    async with async_engine.begin() as conn:
        await conn.run_sync(MappedBase.metadata.drop_all)


def uuid4_str() -> str:
    """数据库引擎 UUID 类型兼容性解决方案"""
    return str(uuid4())


# SQLA 数据库链接
SQLALCHEMY_DATABASE_URL = create_database_url()

# SALA 异步引擎和会话
async_engine = create_database_async_engine(SQLALCHEMY_DATABASE_URL)
async_db_session = create_database_async_session(async_engine)

# Session Annotated
CurrentSession = Annotated[AsyncSession, Depends(get_db)]
CurrentSessionTransaction = Annotated[AsyncSession, Depends(get_db_transaction)]

# new-api 第二数据库引擎（直连 new-api 库）已删除（2026-06-15 解耦）：
# huanxing 不再直连 new-api 数据库，所有 new-api 交互改走 HTTP 管理 API（app/newapi/client.py）。
