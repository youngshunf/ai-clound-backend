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
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='产物类型 (image:图片:blue/voice:语音:purple/video:视频:rose/file:文件:gray/document:文档:cyan/deck:演示文稿:violet/webpage:网页:green/dataset:数据集:orange/other:其它:default)')
    title: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='展示标题 (工具给/文件名/截断的 prompt)')
    summary: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='简要描述')
    body: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='文本/markdown 正文直接入库 (kind=document 文本产物用，不上传文件；二进制走 asset_id，资源走 resource_uri，三选一)')
    asset_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='关联资产 ID (public.hasn_assets.asset_id，image/voice/file 主路径)')
    resource_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='hasn:// 资源 URI (客户端无关，deck/webpage/外部结果无 asset 本体时用)')
    origin_ref: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=f'产出所属业务资源 (resource:plan:todo:{id} 等，来自 work_session.origin_ref，按业务对象反查产物)')
    conversation_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='来源会话 ID (public.hasn_conversations.id)')
    message_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='来源消息 ID (public.hasn_messages.id)')
    session_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='来源本地 runtime session (ULID)')
    source_tool: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='产出工具全名 (hasn.image.generate)')
    source_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='产出来源 (tool_output:工具产出:blue/task_result:任务成果:violet/upload:上传:gray/external:外部结果:orange)')
    dispatch_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='派发关联 (审计/去重)')
    meta_data: Mapped[dict] = mapped_column('metadata', postgresql.JSONB(), default_factory=dict, comment='元数据 (mime/size/width/height 冗余 + 工具上下文快照)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:正常:green/deleted:已删:red)')
