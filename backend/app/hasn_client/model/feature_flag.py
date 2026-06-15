"""D10 内测灰度 feature flag ORM.

依赖规范: docs/架构设计/移动端/08-构建打包与发布详细设计.md §9.1。
M1 决策: 纯国内发行不走 Play Console 灰度 track; 灰度由后端 `feature_flags`
+ `feature_flag_assignments` 白名单表实现。客户端启动时调用
GET /api/v1/app/feature-flags/{hasn_id} 拉取该 hasn_id 启用的 flag 列表。

表 `feature_flags`:
- id (BIGINT PK, 自增)
- key (VARCHAR(64), unique) — flag 唯一 key (e.g. 'im_new_ui')
- description (TEXT, nullable) — 人工备注
- default_enabled (BOOLEAN, default False) — 无 assignment 时的缺省值
- payload (JSONB, nullable) — 可选配置 (客户端读 JSON 驱动新 UI 参数)

表 `feature_flag_assignments`:
- id (BIGINT PK, 自增)
- flag_id (BIGINT FK → feature_flags.id, ON DELETE CASCADE)
- hasn_id (VARCHAR(40), index) — 内测白名单 owner
- enabled (BOOLEAN, default True) — 显式覆盖 default_enabled
- 唯一索引 (flag_id, hasn_id) — 同一 flag 每个 hasn_id 仅一行

Alembic 迁移: backend/alembic/versions/20260421_d10_create_feature_flags.py
"""
from __future__ import annotations

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class FeatureFlag(Base):
    """feature_flags 表 (D10)."""

    __tablename__ = 'feature_flags'
    __table_args__ = (
        sa.UniqueConstraint('key', name='uq_feature_flags_key'),
        {'comment': 'M1 内测灰度 feature flag 注册表 [D10]'},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    key: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default='', comment='flag 唯一 key'
    )
    description: Mapped[str | None] = mapped_column(
        sa.Text(), nullable=True, default=None, comment='人工备注'
    )
    default_enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        comment='无 assignment 时的缺省启用状态',
    )
    payload: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(),
        nullable=True,
        default=None,
        comment='可选配置 JSON (客户端读)',
    )


class FeatureFlagAssignment(Base):
    """feature_flag_assignments 表 (D10) — 内测白名单."""

    __tablename__ = 'feature_flag_assignments'
    __table_args__ = (
        sa.UniqueConstraint(
            'flag_id',
            'hasn_id',
            name='uq_feature_flag_assignments_flag_hasn',
        ),
        sa.Index('ix_feature_flag_assignments_hasn_id', 'hasn_id'),
        {'comment': 'M1 内测灰度白名单 (按 hasn_id 覆盖 default_enabled) [D10]'},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    flag_id: Mapped[int] = mapped_column(
        sa.BigInteger(),
        sa.ForeignKey('feature_flags.id', ondelete='CASCADE'),
        nullable=False,
        default=0,
        comment='关联 feature_flags.id',
    )
    hasn_id: Mapped[str] = mapped_column(
        sa.String(40), nullable=False, default='', comment='内测白名单 hasn_id'
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=True,
        comment='显式覆盖 default_enabled',
    )
