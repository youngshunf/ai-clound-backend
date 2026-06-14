from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class SiteSchemaBase(SchemaBase):
    """已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）基础模型"""
    owner_id: str = Field(description='发布者 owner HASN ID（owner 隔离键，引用 public.hasn_humans）')
    publisher_agent_id: str | None = Field(None, description='若由 agent 代发布，记发起分身 HASN ID（审计，可空）')
    kind: str = Field(description='制品类型 (deck:演示文稿:violet/report:报告:blue/page:单页:green/dashboard:看板:orange/other:其它:gray)')
    title: str = Field(description='展示标题')
    slug: str = Field(description='不可枚举短码（base62 ≥10 位），分享路径 /s/{slug}')
    source_app: str | None = Field(None, description='来源应用（deck 等，便于回到来源编辑，可空）')
    source_ref: str | None = Field(None, description='来源实体 id（如 deck_id，便于更新/反查，可空）')
    current_revision_id: int | None = Field(None, description='当前对外版本指针（引用 hasn_publish.revision.id，可空）')
    status: str = Field(description='状态 (active:生效:green/revoked:已撤销:gray)')
    visibility: str = Field(description='可见性 (private:私有:gray/password:口令:orange/unlisted:不公开:blue/public:公开:green)')
    password_hash: str | None = Field(None, description='visibility=password 时存 bcrypt hash（绝不存明文，可空）')
    expires_at: datetime | None = Field(None, description='过期即拒访（含 unlisted/public，可空）')
    allow_present: bool = Field(description='是否允许放映/演讲者模式')
    allow_download: bool = Field(description='是否允许下载原始制品')
    allow_indexing: bool = Field(description='visibility=public 时是否允许公开收录/搜索引擎索引（默认不收录；unlisted 恒 noindex）')
    view_count: int = Field(description='访问计数（统计，非鉴权）')
    rev: int = Field(description='元数据乐观锁/同步游标（每次写 +1）')
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删）')


class CreateSiteParam(SiteSchemaBase):
    """创建已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）参数"""


class UpdateSiteParam(SiteSchemaBase):
    """更新已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）参数"""


class DeleteSiteParam(SchemaBase):
    """删除已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）参数"""

    pks: list[int] = Field(description='已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针） ID 列表')


class GetSiteDetail(SiteSchemaBase):
    """已发布制品（云端权威：稳定身份 + slug + 可见性 + 当前版本指针）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
