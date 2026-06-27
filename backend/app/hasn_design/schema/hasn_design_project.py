from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnDesignProjectSchemaBase(SchemaBase):
    """设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）基础模型"""

    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键；= 设计 §5.9-2 的 owner_id 数据隔离）')
    name: str = Field(description='项目名（= OpenPencil 文档名）')
    description: str | None = Field(None, description='项目说明')
    thumbnail_asset_uri: str | None = Field(
        None, description='缩略图资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）'
    )
    bound_agent_id: str | None = Field(
        None, description='绑定设计分身 hasn_id（BoundAgentControl，对齐 deck/studio bound_agent_id）'
    )
    canvas_meta: dict = Field(description='画布轻元数据 jsonb（{width,height,page_count}）')
    latest_artifact_id: str | None = Field(
        None, description='最近导出产物公开标识（public.hasn_artifacts.artifact_id，art_<ulid>；非硬 FK）'
    )
    enterprise_id: str | None = Field(None, description='企业归属 id（GE 双模：个人项目为空，企业项目归企业）')
    status: str = Field(description='状态 (draft:草稿:blue/active:活跃:green/archived:归档:gray)')
    visibility: str = Field(description='可见性 (private:私有:gray/shared:已分享:blue/public:公开:green)')


class CreateHasnDesignProjectParam(HasnDesignProjectSchemaBase):
    """创建设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）参数"""


class UpdateHasnDesignProjectParam(HasnDesignProjectSchemaBase):
    """更新设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）参数"""


class DeleteHasnDesignProjectParam(SchemaBase):
    """删除设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）参数"""

    pks: list[int] = Field(description='设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先） ID 列表')


class GetHasnDesignProjectDetail(HasnDesignProjectSchemaBase):
    """设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
