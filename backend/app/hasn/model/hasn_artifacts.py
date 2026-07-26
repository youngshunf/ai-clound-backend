from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnArtifacts(Base):
    """分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）"""

    __tablename__ = 'hasn_artifacts'

    id: Mapped[id_key] = mapped_column(init=False)
    artifact_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='产物 ID (art_<ulid> 公开标识)')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='产出分身 hasn_id')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='分身主人 hasn_id (归属 + 隔离键)')
    artifact_key: Mapped[str] = mapped_column(sa.String(768), default='', comment='主人内稳定对象键')
    artifact_kind: Mapped[str] = mapped_column(sa.String(16), default='resource', comment='产物打开类型')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='产物类型·怎么打开 (resource:应用资源:violet/document:文档:cyan/image:图片:blue/video:视频:rose/voice:语音:purple/file:文件:gray)')
    resource_kind: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='应用资源类型 (descriptor.resource_kind 原值，如 knowledge.base/deck.presentation；仅 artifact_kind=resource 有值；UI 据它查 registry 取展示名)')
    resource_app_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='资源所属应用 ID')
    title: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='展示标题 (工具给/文件名/截断的 prompt)')
    summary: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='简要描述')
    body: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='文本/markdown 正文直接入库；二进制走 asset_id，资源走 resource_uri，本地文件走不可逆定位键')
    asset_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='关联资产 ID (public.hasn_assets.asset_id，image/voice/file 主路径)')
    resource_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='hasn:// 资源 URI (客户端无关，deck/webpage/外部结果无 asset 本体时用)')
    local_locator_key: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='不可逆本地对象定位键')
    local_entry_kind: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='本地条目类型')
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产出设备节点 ID（本地定位键产物必填）')
    origin_ref: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=f'产出所属业务资源 (resource:plan:todo:{id} 等，来自 work_session.origin_ref，按业务对象反查产物)')
    conversation_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='来源会话 ID (public.hasn_conversations.id)')
    message_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='来源消息 ID (public.hasn_messages.id)')
    session_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='来源本地 runtime session (ULID)')
    project_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='平台项目挂靠 id (hasn_project.hasn_project.id，可空；register-on-write 经 ContextVar 自动打标，doc38 §5.1；不是权限边界，仅聚合过滤键)')
    source_tool: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='产出工具全名 (hasn.image.generate)')
    source_app_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='来源应用 ID (hasn_app_catalog.app_id，如 deck/imagelab/knowledge；UI 据此显示应用图标，非应用产出为空)')
    source_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='产出来源·怎么来的 (app:应用产出:violet/platform_tool:平台工具:blue/external_tool:外部取材:orange/runtime_file:运行时文件:gray/agent_note:分身自撰:cyan/upload:主人上传:default)')
    action: Mapped[str] = mapped_column(sa.String(16), default='create', comment='产出动作 (create:新增:green/update:修改:blue)')
    dispatch_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='派发关联 (审计/去重)')
    meta_data: Mapped[dict] = mapped_column('metadata', postgresql.JSONB(), default_factory=dict, comment='元数据 (mime/size/width/height 冗余 + 工具上下文快照)')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
