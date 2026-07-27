from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnAgents(Base):
    """HASN Agent 表（统一实体架构 v5.0）"""

    __tablename__ = 'hasn_agents'

    id: Mapped[id_key] = mapped_column(init=False)
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='HASN Agent 唯一标识（格式: a_{uuid}）')
    star_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Agent 唤星号（如: 100001#star）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属 Human 的 hasn_id')
    display_name: Mapped[str] = mapped_column(sa.String(100), default='', comment='Agent 显示名（支持中文，对外展示）')
    agent_name: Mapped[str] = mapped_column(sa.String(30), default='', comment='Agent 标识名')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='Agent 描述')
    avatar: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='头像（与 sys_user.avatar 对齐）')
    bio: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='Agent 简介（社区主页展示）')
    profile_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='Agent 扩展档案 JSON（含 community 子结构：边界/内容声明/置顶）'
    )
    capability_summary_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='Agent 能力概览 JSON（擅长领域/可执行技能，社区主页展示）'
    )
    runtime_config_json: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(),
        default=None,
        comment='Agent hermes runtime 原生配置（4 槽模型/工作目录/max_turns/网关超时/记忆开关/时区；云端权威，下发写入 runtime config.yaml/.env）',
    )
    runtime_location: Mapped[str] = mapped_column(
        sa.String(16),
        default='local',
        comment='运行位置 (local:本地:blue/cloud:云端:green)',
    )
    type: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='Agent 类型 (desktop:桌面端:blue/mobile:手机端:green/cloud:云端:purple/web:网页端:orange)',
    )
    role: Mapped[str] = mapped_column(
        sa.String(20),
        default='specialist',
        comment='Agent 角色 (primary:主要:blue/specialist:专家:green/service:服务:orange)',
    )
    profession: Mapped[str | None] = mapped_column(
        sa.String(60),
        default=None,
        comment='领域专家头衔（如「金融专家」，来自模板 name；与人名 display_name 并列展示）',
    )
    builtin_agent_key: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='内置 agent 类型键（来自模板 builtin_key）；非内置 agent 为 NULL',
    )
    node_id: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='Agent 驻留节点 ID（设备指纹派生，格式: n_{hash}）'
    )
    capabilities: Mapped[dict | list | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='Agent 能力列表（A2A AgentCard 兼容 JSONB）'
    )
    template_id: Mapped[str | None] = mapped_column(
        sa.String(80), default=None, comment='Agent 模板 ID（来自 Agent 市场，可空表示自定义）'
    )
    template_version: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='Agent 模板版本（创建时快照，可空）'
    )
    skills: Mapped[dict | list | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='Agent 技能配置 JSON（云端 Profile 配置源）'
    )
    skill_bundles: Mapped[list | None] = mapped_column(
        postgresql.JSONB(), default_factory=list,
        comment='已安装技能包引用 [{template_id, version}]（成员技能另并入 skills）',
    )
    soul_md: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='Agent SOUL.md 内容（云端 Profile 配置源）'
    )
    agents_md: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='Agent AGENTS.md 内容（云端 Profile 配置源）'
    )
    user_md: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='Agent USER.md 内容（云端 Profile 配置源）'
    )
    memory_md: Mapped[str | None] = mapped_column(
        UniversalText, default=None, comment='Agent MEMORY.md 内容（Agent 自我演化记忆，云端 Profile 配置源）'
    )
    profile_source: Mapped[str] = mapped_column(
        sa.String(20), default='cloud', comment='Profile 来源 (cloud:云端事实源:green/imported:导入:blue)'
    )
    profile_revision: Mapped[int] = mapped_column(sa.BigInteger, default=1, comment='Agent Profile 修订号')
    api_key_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent Key 的 SHA256 哈希')
    tags: Mapped[list[str] | None] = mapped_column(
        postgresql.JSONB(),
        default_factory=list,
        comment='Agent 标签数组（云端权威，daemon 仅镜像）',
    )
    capability_set_id: Mapped[str | None] = mapped_column(
        sa.String(80), default=None, comment='Agent 能力集 ID（与 hasn_agent_capabilities 关联，云端权威）'
    )
    persona_ref: Mapped[str | None] = mapped_column(
        sa.String(120), default=None, comment='Agent persona 引用（template / persona 资产 ID，云端权威）'
    )
    status: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='状态/生命周期 (active:活跃:green/disabled:已停用:orange/revoked:已吊销:red/archived:已归档:gray/deleted:已删除:gray)',
    )
    created_via: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment='创建来源 (guardian:Guardian注册:blue/client:客户端创建:green/ws:WS实时注册:purple)',
    )
    social_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, default=True, comment='是否对外开启社交可见 (true:社交可见/false:仅自用)，默认开启，用户可手动关闭'
    )
    inbound_policy: Mapped[str] = mapped_column(
        sa.String(20),
        default='auto',
        comment='外部入站消息门控策略 (auto:自动放行:green/manual_strangers:陌生人需放行:orange/manual_all:全部需放行:red)，默认自动，主人可设手动审阅',
    )
    binding_node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='Agent 当前绑定的 node ID')
    binding_status: Mapped[str] = mapped_column(
        sa.String(32),
        default='unbound',
        comment='binding 状态 (unbound:未绑定:gray/binding:绑定中:blue/bound:已绑定:green/failed:绑定失败:red)',
    )
    binding_updated_at: Mapped[int | None] = mapped_column(
        sa.BigInteger, default=None, comment='binding 状态更新时间（Unix 秒）'
    )
    online_status: Mapped[str] = mapped_column(
        sa.String(32), default='offline', comment='在线状态 (offline:离线:gray/online:在线:green)'
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(default=None, comment='最后心跳时间（用于超时检测）')
    deleted_at: Mapped[datetime | None] = mapped_column(default=None, comment='软删除时间')
