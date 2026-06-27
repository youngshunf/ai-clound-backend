from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ReelProjectSchemaBase(SchemaBase):
    """短视频项目（reel：一组创作的容器 + 默认创作参数）基础模型"""
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: str | None = Field(None, description='创建/默认协作分身 hasn_id（创建带归属资源默认取凭证身份）')
    title: str = Field(description='项目标题（如「秋季热饮系列」）')
    description: str | None = Field(None, description='项目说明')
    settings: dict = Field(description='默认创作参数 jsonb（比例/单段时长/音色/素材源/字幕/调性，喂创作缺省）')
    cover_asset_uri: str | None = Field(None, description='封面资产 hasn://asset/（取首条成片首帧；序列化边界换 CDN 签名 URL，不存直链）')
    bound_agent_id: str | None = Field(None, description='项目绑定协作分身 hasn_id（BoundAgentControl，对齐 CRX-3/DECKBIND）')
    status: str = Field(description='状态 (active:进行中:green/archived:已归档:gray)')


class CreateReelProjectParam(ReelProjectSchemaBase):
    """创建短视频项目（reel：一组创作的容器 + 默认创作参数）参数"""


class UpdateReelProjectParam(ReelProjectSchemaBase):
    """更新短视频项目（reel：一组创作的容器 + 默认创作参数）参数"""


class DeleteReelProjectParam(SchemaBase):
    """删除短视频项目（reel：一组创作的容器 + 默认创作参数）参数"""

    pks: list[int] = Field(description='短视频项目（reel：一组创作的容器 + 默认创作参数） ID 列表')


class GetReelProjectDetail(ReelProjectSchemaBase):
    """短视频项目（reel：一组创作的容器 + 默认创作参数）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
