from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnArtifactsSchemaBase(SchemaBase):
    """分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）基础模型"""
    artifact_id: str = Field(description='产物 ID (art_<ulid> 公开标识)')
    agent_hasn_id: str = Field(description='产出分身 hasn_id')
    owner_hasn_id: str = Field(description='分身主人 hasn_id (归属 + 隔离键)')
    kind: str = Field(description='产物类型 (image:图片:blue/voice:语音:purple/file:文件:gray/document:文档:cyan/deck:演示文稿:violet/webpage:网页:green/dataset:数据集:orange/other:其它:default)')
    title: str | None = Field(None, description='展示标题 (工具给/文件名/截断的 prompt)')
    summary: str | None = Field(None, description='简要描述')
    body: str | None = Field(None, description='文本/markdown 正文直接入库 (kind=document 文本产物用，不上传文件)')
    asset_id: str | None = Field(None, description='关联资产 ID (public.hasn_assets.asset_id，image/voice/file 主路径)')
    resource_uri: str | None = Field(None, description='hasn:// 资源 URI (客户端无关，deck/webpage/外部结果无 asset 本体时用)')
    origin_ref: str | None = Field(None, description='产出所属业务资源 (resource:plan:todo:{id} 等，按业务反查)')
    conversation_id: str | UUID | None = Field(None, description='来源会话 ID (public.hasn_conversations.id)')
    message_id: int | None = Field(None, description='来源消息 ID (public.hasn_messages.id)')
    session_id: str | None = Field(None, description='来源本地 runtime session (ULID)')
    source_tool: str | None = Field(None, description='产出工具全名 (hasn.image.generate)')
    source_kind: str = Field(description='产出来源 (tool_output:工具产出:blue/task_result:任务成果:violet/upload:上传:gray/external:外部结果:orange)')
    dispatch_id: str | None = Field(None, description='派发关联 (审计/去重)')
    meta_data: dict = Field(description='元数据 (mime/size/width/height 冗余 + 工具上下文快照)')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateHasnArtifactsParam(HasnArtifactsSchemaBase):
    """创建分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）参数"""


class UpdateHasnArtifactsParam(HasnArtifactsSchemaBase):
    """更新分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）参数"""


class DeleteHasnArtifactsParam(SchemaBase):
    """删除分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）参数"""

    pks: list[int] = Field(description='分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针） ID 列表')


class GetHasnArtifactsDetail(HasnArtifactsSchemaBase):
    """分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


# ============================================================================
# 产物系统专用请求/响应模型（AF-2）：身份不入参，由凭证自识别；序列化经 resolve_assets。
# ============================================================================


class RecordArtifactParam(SchemaBase):
    """Agent 登记一条产物的入参（不含 agent/owner 身份字段——身份取自 Agent JWT）。"""

    kind: str = Field(
        'other',
        description='产物类型 (image/voice/file/document/deck/webpage/dataset/other)',
    )
    title: str | None = Field(None, description='展示标题')
    summary: str | None = Field(None, description='简要描述')
    body: str | None = Field(None, description='文本/markdown 正文直接入库（kind=document 文本产物用，不上传文件）')
    asset_id: str | None = Field(None, description='关联资产 ID（image/voice/file 主路径）')
    resource_uri: str | None = Field(None, description='hasn:// 资源 URI（deck/webpage 等无 asset 本体时用）')
    local_path: str | None = Field(
        None,
        description='本地绝对路径（本地权威产物，云端只存指针不存正文；必须同时给 node_id）',
    )
    node_id: str | None = Field(None, description='产出设备节点 ID（给 local_path 时必填）')
    origin_ref: str | None = Field(None, description='产出所属业务资源（resource:plan:todo:{id} 等，按业务反查）')
    conversation_id: str | None = Field(None, description='来源会话 ID（UUID 字符串）')
    message_id: int | None = Field(None, description='来源消息 ID')
    session_id: str | None = Field(None, description='来源本地 runtime session（ULID）')
    source_tool: str | None = Field(None, description='产出工具全名（hasn.image.generate）')
    source_app_id: str | None = Field(None, description='来源应用 ID（deck/imagelab/knowledge…；UI 据此显示应用图标）')
    source_kind: str = Field('tool_output', description='产出来源 (tool_output/task_result/upload/external)')
    action: str = Field('create', description='产出动作 (create:新增 / update:修改)')
    dispatch_id: str | None = Field(None, description='派发关联（审计/去重）')
    metadata: dict = Field(default_factory=dict, description='元数据快照（mime/size/width/height 等）')


class RecordArtifactResult(SchemaBase):
    """登记结果（返回 artifact_id；去重命中时返回既有 id）。"""

    artifact_id: str = Field(description='产物 ID')


class UpdateArtifactContentParam(SchemaBase):
    """Owner 更新产物正文的入参（markdown 编辑保存用）。

    只允许改 body/title——asset_id/resource_uri 等指针不可改（产物溯源语义不变）。
    对 asset 型 .md 文件产物：编辑保存写 body（body 成为权威正文，原 asset 保留为「原文件」可下载）。
    """

    body: str = Field(description='文本/markdown 正文（编辑后全文覆盖）')
    title: str | None = Field(None, description='展示标题（可选，一并更新）')


class ArtifactItem(SchemaBase):
    """产物列表项（已派生跳转链接；图片含签名 display_url）。"""

    artifact_id: str
    kind: str
    title: str | None = None
    summary: str | None = None
    body: str | None = Field(None, description='文本/markdown 正文（kind=document 文本产物，前端内联渲染）')
    asset_id: str | None = None
    resource_uri: str | None = None
    origin_ref: str | None = Field(None, description='产出所属业务资源（resource:plan:todo:{id} 等）')
    conversation_id: str | None = None
    message_id: int | None = None
    session_id: str | None = None
    source_tool: str | None = None
    source_kind: str = 'tool_output'
    source_link: str | None = Field(None, description='点击跳转来源的 hasn:// URI')
    display_url: str | None = Field(None, description='可展示签名 URL（图片缩略图/预览，有时效）')
    created_time: datetime


class ArtifactDetail(ArtifactItem):
    """产物详情（额外含下载 URL 与元数据）。"""

    download_url: str | None = Field(None, description='下载签名 URL（有时效）')
    metadata: dict = Field(default_factory=dict)


class ArtifactListData(SchemaBase):
    """产物分页列表信封。"""

    items: list[ArtifactItem]
    total: int
    page: int
    size: int
