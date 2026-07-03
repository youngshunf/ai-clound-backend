"""Phase 7 backend HASN 测试 fixtures (in-memory aiosqlite)。

策略：构造一个独立的 SQLAlchemy MetaData，仅声明 audit_log 因果链测试需要的最小 schema，
不复用业务 ORM（避免 PostgreSQL JSONB / 跨表 FK 与 SQLite 不兼容）。

被测代码 (hasn_audit_log_service.append) 在函数体内 import 自己的 ORM 模型，因此通过
monkeypatch 把 module-level HasnAuditLog 替换为 SQLite-friendly 的 AuditLogStub。
"""
from __future__ import annotations

import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _TestBase(DeclarativeBase):
    """孤立的 declarative base，仅供本测试模块内使用。"""


class AuditLogStub(_TestBase):
    """与生产 HasnAuditLog 字段名/类型对齐的 SQLite 友好镜像。"""

    __tablename__ = 'hasn_audit_log'

    # SQLite ROWID 仅对 INTEGER PRIMARY KEY 自动 autoincrement；BigInteger 在 SQLite 上
    # 不会触发 ROWID alias，需要显式 Integer 才能让 INSERT 自填主键。生产 Postgres 用 BIGINT，
    # 这里测试 stub 退化为 INTEGER 不影响 hash_chain / prev_log_id 因果链语义。
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(sa.String(36), default='')
    actor_type: Mapped[str] = mapped_column(sa.String(10), default='')
    action: Mapped[str] = mapped_column(sa.String(50), default='')
    target_type: Mapped[str | None] = mapped_column(sa.String(20), default=None, nullable=True)
    target_id: Mapped[str | None] = mapped_column(sa.String(36), default=None, nullable=True)
    details: Mapped[dict] = mapped_column(sa.JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(sa.String(45), default=None, nullable=True)
    prev_log_id: Mapped[int | None] = mapped_column(
        sa.BigInteger, sa.ForeignKey('hasn_audit_log.id'), default=None, nullable=True
    )
    hash_chain: Mapped[str] = mapped_column(sa.String(64), default='')
    findings: Mapped[list] = mapped_column(sa.JSON, default=list)
    severity: Mapped[str | None] = mapped_column(sa.String(16), default=None, nullable=True)


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    """提供 in-memory SQLite AsyncSession，并 monkeypatch 被测 service 的 ORM 引用。"""
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    # service.append() 在函数体内 import HasnAuditLog；用 stub 替换 module attribute。
    import backend.app.hasn.model.hasn_audit_log as model_mod
    monkeypatch.setattr(model_mod, 'HasnAuditLog', AuditLogStub, raising=True)

    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()

    await engine.dispose()
