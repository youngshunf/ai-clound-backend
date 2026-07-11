"""
创作运营（hasn_creator）测试基础设施 conftest。

提供指向本地开发 PostgreSQL（127.0.0.1:15432 / huanxing）的**异步** SQLAlchemy
session fixture——CreatorService 全为 async + ORM，故需 AsyncSession（sync 连接跑不了）。
每个测试用例开一个事务，测试结束回滚，保持隔离、不污染库。
"""

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 本地开发数据库（PostgreSQL 127.0.0.1:15432，库名 huanxing，用户 mac；与 tests/hasn/conftest.py 同源）
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'


@pytest_asyncio.fixture(scope='function')
async def db_session() -> AsyncSession:
    """异步 session（事务内跑，结束回滚，测试隔离）。"""
    engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        trans = await session.begin()
        try:
            yield session
        finally:
            await trans.rollback()
    await engine.dispose()
