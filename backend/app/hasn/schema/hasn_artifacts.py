from datetime import datetime
from hashlib import sha256
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from backend.common.schema import SchemaBase
from backend.app.hasn.schema.artifact_contract import ArtifactAction, ArtifactKind, ArtifactSourceKind, LocalEntryKind

# ============================================================================
# 产物分类三维度（doc35）：一个字段一个维度，不互相僭越。
#
# - artifact_kind：只回答「怎么打开」——与「本体存在哪」严格 1:1（doc35 §3/§6 I1–I3）。
# - resource_kind：只回答「是什么」——descriptor.resource_kind 原值，仅应用资源有值（§4）。
# - source_app_id：只回答「哪个应用」（doc34 已有列）。
# - source_kind：只回答「怎么来的 / 谁产的」（§5）。
#
# 这两个 Literal 是**拒绝**而非归一：越界 → 422。旧白名单静默改写成 other 才是
# 「模板声明 kind → 分身真产出了 → 登记被降级 → 闸门比对不上 → 判定未产出」死锁的根因（§1.5）。
# ============================================================================
class HasnArtifactsSchemaBase(SchemaBase):
    """分身产物登记表（分身产出的图片/文件/文档/演示文稿/网页等的溯源指针）基础模型"""
    artifact_id: str = Field(description='产物 ID (art_<ulid> 公开标识)')
    agent_hasn_id: str = Field(description='产出分身 hasn_id')
    owner_hasn_id: str = Field(description='分身主人 hasn_id (归属 + 隔离键)')
    kind: ArtifactKind = Field(description='产物类型·怎么打开 (resource:应用资源/document:文档/image:图片/video:视频/voice:语音/file:文件)')
    resource_kind: str | None = Field(None, description='应用资源类型 (descriptor.resource_kind 原值，如 knowledge.base；仅 kind=resource 有值)')
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
    source_kind: ArtifactSourceKind = Field(description='产出来源·怎么来的 (app/platform_tool/external_tool/runtime_file/agent_note/upload)')
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

    # 无缺省：kind 必须显式声明。旧的 default='other' 让「没想清楚产的是什么」也能过——
    # 而 other 正是本次要根除的那个坟场（doc35 §3.1）。
    kind: ArtifactKind = Field(
        description='产物类型·怎么打开 (resource:应用资源 / document:body 存 markdown / image / video / voice / file)',
    )
    resource_kind: str | None = Field(
        None,
        description='应用资源类型（descriptor.resource_kind 原值，如 knowledge.base；仅 kind=resource 时给）',
    )
    title: str | None = Field(None, description='展示标题')
    summary: str | None = Field(None, description='简要描述')
    body: str | None = Field(None, description='文本/markdown 正文直接入库（kind=document 文本产物用，不上传文件）')
    asset_id: str | None = Field(None, description='关联资产 ID（image/voice/file 主路径）')
    resource_uri: str | None = Field(None, description='hasn:// 资源 URI（deck/webpage 等无 asset 本体时用）')
    local_locator_key: str | None = Field(None, description='经节点守卫生成的不可逆本地对象定位键')
    local_entry_kind: LocalEntryKind | None = Field(None, description='本地条目类型')
    node_id: str | None = Field(None, description='产出设备节点 ID（给 local_locator_key 时必填）')
    origin_ref: str | None = Field(None, description='产出所属业务资源（resource:plan:todo:{id} 等，按业务反查）')
    conversation_id: str | None = Field(None, description='来源会话 ID（UUID 字符串）')
    message_id: int | None = Field(None, description='来源消息 ID')
    session_id: str | None = Field(None, description='来源本地 runtime session（ULID）')
    # 本地 transport 应用（reel/film/design/imagelab/publish）走 /artifacts/agent/record 通道、
    # 不经云端 ContextVar 打标，故项目根产出必须把云端权威 project_id 随入参上报（P9-C 项目轴）。
    project_id: str | None = Field(
        None,
        description='所属平台项目云端权威 id（hasn_project.id；聚合过滤键，非权限边界）',
    )
    source_tool: str | None = Field(None, description='产出工具全名（hasn.image.generate）')
    source_app_id: str | None = Field(None, description='来源应用 ID（deck/imagelab/knowledge…；UI 据此显示应用图标）')
    source_kind: ArtifactSourceKind = Field(
        description='产出来源（app_write/platform_tool/runtime_file/agent_note/external_import）',
    )
    action: ArtifactAction = Field('create', description='产出动作 (create:新增 / update:修改)')
    dispatch_id: str | None = Field(None, description='派发关联（审计/去重）')
    metadata: dict = Field(default_factory=dict, description='元数据快照（mime/size/width/height 等）')

    @model_validator(mode='before')
    @classmethod
    def normalize_legacy_runtime_path(cls, data: object) -> object:
        """将冻结 runtime sink 的旧路径入参即时归一为不可逆定位键，不保留原路径。"""
        if not isinstance(data, dict):
            return data
        legacy_path = data.get('local_path')
        if not isinstance(legacy_path, str) or not legacy_path:
            return data
        normalized = dict(data)
        normalized.pop('local_path', None)
        if normalized.get('local_locator_key') is None:
            digest = sha256(legacy_path.encode('utf-8')).hexdigest()
            normalized['local_locator_key'] = f'legacy-path-v1:{digest}'
        normalized.setdefault('local_entry_kind', 'file')
        return normalized


class RecordArtifactResult(SchemaBase):
    """登记结果（返回 artifact_id；去重命中时返回既有 id）。"""

    artifact_id: str = Field(description='产物 ID')


class UpdateArtifactContentParam(SchemaBase):
    """Owner 更新产物正文的入参（markdown 编辑保存用）。

    只允许编辑文本产物的 body/title；asset_id/resource_uri/local locator 型产物必须使用各自原始编辑入口。
    """

    body: str = Field(description='文本/markdown 正文（编辑后全文覆盖）')
    title: str | None = Field(None, description='展示标题（可选，一并更新）')


class ArtifactItem(SchemaBase):
    """产物列表项（已派生跳转链接；图片含签名 display_url）。"""

    artifact_id: str
    kind: ArtifactKind
    resource_kind: str | None = Field(
        None,
        description='应用资源类型（如 knowledge.base；仅 kind=resource 有值。UI 据它查 registry 取展示名/图标，零硬编码）',
    )
    title: str | None = None
    summary: str | None = None
    body: str | None = Field(None, description='文本/markdown 正文（kind=document 文本产物，前端内联渲染）')
    asset_id: str | None = None
    resource_uri: str | None = None
    local_path: str | None = Field(None, description='本地文件绝对路径（本地产物；正文留在产出设备磁盘，云端只存指针）')
    node_id: str | None = Field(None, description='产出设备 node_id（local_path 在场必带；UI 据此判本机可开还是在其他设备）')
    origin_ref: str | None = Field(None, description='产出所属业务资源（resource:plan:todo:{id} 等）')
    agent_hasn_id: str | None = Field(
        None,
        description='产出该产物的分身 hasn_id（doc97：项目内跨分身查产物时，据它判断这条是哪一环产的）',
    )
    conversation_id: str | None = None
    message_id: int | None = None
    session_id: str | None = None
    source_tool: str | None = None
    source_app_id: str | None = Field(None, description='来源应用 id（UI 据此显示应用图标；非应用产出留空）')
    source_kind: ArtifactSourceKind
    action: str = Field('create', description='产出动作 (create:新增/update:修改)')
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
