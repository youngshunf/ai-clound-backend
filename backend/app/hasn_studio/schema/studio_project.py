from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class StudioProjectSchemaBase(SchemaBase):
    """视频项目（统一视频引擎 studio：管线/素材/成品的容器）基础模型"""
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: str | None = Field(None, description='创建/默认协作分身 hasn_id（创建带归属资源默认取凭证身份，PLANFIX-6）')
    title: str = Field(description='项目标题')
    description: str | None = Field(None, description='项目说明')
    default_pipeline_key: str | None = Field(None, description='项目默认管线 key（来自引擎 pipeline_defs，非 PG 表）')
    settings: dict = Field(description='项目设置 jsonb（调性/分辨率/默认参数/品牌，喂 run_pipeline 缺省）')
    cover_asset_uri: str | None = Field(None, description='封面资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）')
    bound_agent_id: str | None = Field(None, description='项目绑定协作分身 hasn_id（BoundAgentControl，对齐 CRX-3/DECKBIND）')
    status: str = Field(description='状态 (draft:草稿:blue/active:进行中:green/archived:已归档:gray)')


class CreateStudioProjectParam(StudioProjectSchemaBase):
    """创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数"""


class UpdateStudioProjectParam(StudioProjectSchemaBase):
    """更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数"""


class DeleteStudioProjectParam(SchemaBase):
    """删除视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数"""

    pks: list[int] = Field(description='视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID 列表')


class GetStudioProjectDetail(StudioProjectSchemaBase):
    """视频项目（统一视频引擎 studio：管线/素材/成品的容器）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
