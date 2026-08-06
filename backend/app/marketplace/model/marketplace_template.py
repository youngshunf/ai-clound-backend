from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from backend.app.marketplace.model._base import APP_SCHEMA, MarketplaceBase
from backend.common.model import TimeZone, UniversalText, id_key


class MarketplaceTemplate(MarketplaceBase):
    """技能市场模板表（Agent模板/技能包/SOP包）"""

    __tablename__ = 'marketplace_template'

    @declared_attr.directive
    def __table_args__(cls):  # noqa: N805
        # 内置 agent 类型键全局唯一（仅非空时约束）
        return (
            sa.Index(
                'uq_marketplace_template_builtin_key',
                'builtin_key',
                unique=True,
                postgresql_where=sa.text('builtin_key IS NOT NULL'),
            ),
            {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA},
        )

    id: Mapped[id_key] = mapped_column(init=False)
    template_id: Mapped[str] = mapped_column(sa.String(255), default='', comment='模板唯一标识')
    namespace: Mapped[str | None] = mapped_column(
        sa.String(160),
        default=None,
        comment='命名空间（如 huanxing/clawhub）',
    )
    slug: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='模板标识符')
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='资源所有者用户ID')
    hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='资源所有者 HASN ID')
    status: Mapped[str] = mapped_column(sa.String(20), default='published', comment='发布状态')
    visibility: Mapped[str] = mapped_column(sa.String(20), default='public', comment='可见性')
    requested_visibility: Mapped[str] = mapped_column(
        sa.String(20),
        default='private',
        comment='草稿期记录的期望可见性；审核完成前实际 visibility 恒为 private',
    )
    reviewed_by: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='审核人用户ID')
    reviewed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='审核时间')
    review_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='审核备注')
    published_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发布时间')
    suspended_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='封禁时间')
    suspend_reason: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='封禁原因')
    template_type: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='模板类型 (agent_template:Agent模板:blue/skill_pack:技能包:cyan/sop_pack:SOP包:purple)',
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='模板名称')
    name_en: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='英文名称')
    name_zh: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='中文名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='模板描述')
    description_en: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='英文描述')
    description_zh: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='中文描述')
    source_language: Mapped[str | None] = mapped_column(
        sa.String(10),
        default=None,
        comment='源语言（en/zh，用于判断哪个是原文）',
    )
    icon_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='模板图标URL')
    emoji: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='emoji图标')
    author_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='作者用户ID')
    author_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='作者名称')
    pricing_type: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='定价类型 (free:免费:green/paid:付费:orange/subscription:订阅:blue)',
    )
    price: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='价格')
    is_private: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否私有')
    is_official: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否官方模板')
    is_common: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='是否公共技能包（默认叠加进每个 Agent 的能力清单）'
    )
    builtin: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='是否内置 agent 模板（注册时自动创建）'
    )
    builtin_key: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='内置 agent 类型键（内置任务 target_agent_type 按此匹配）',
    )
    download_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='下载次数')
    category: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='分类')
    tags: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='标签，逗号分隔')
    name_pool: Mapped[str | None] = mapped_column(
        sa.String(500),
        default=None,
        comment='候选人名池（逗号分隔，仅 Agent 模板；创建分身时供用户选择/兜底，display_name 取池首位）',
    )
    source_type: Mapped[str | None] = mapped_column(
        sa.String(20),
        default=None,
        comment='来源类型 (github:GitHub:blue/clawhub:ClawHub:green/local:本地:gray)',
    )
    source_repo_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='源仓库 URL')
    source_repo_path: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='仓库内路径')
    skill_dependencies: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='依赖的技能ID列表，逗号分隔',
    )
    soul_md: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='模板 SOUL.md 内容（Agent 身份档案，创建时物化进 hasn_agents.soul_md）',
    )
    agents_md: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='模板 AGENTS.md 内容（Agent 行为指南，创建时物化进 hasn_agents.agents_md）',
    )
    user_md: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='模板 USER.md 内容（主人信息种子，创建时物化进 hasn_agents.user_md）',
    )
    memory_md: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='模板 MEMORY.md 内容（Agent 长期/自我演化记忆种子，§ 记录格式，创建时种子进 hasn_agents.memory_md）',
    )
    sop_dependencies: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment='依赖的SOP ID列表，逗号分隔',
    )
    repo_path: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='在 hasn-hub 中的路径')
    git_commit_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最新同步的 commit hash')
    synced_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后同步时间')
    translated_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后翻译时间')
