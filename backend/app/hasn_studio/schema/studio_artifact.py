from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class StudioArtifactSchemaBase(SchemaBase):
    """视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）基础模型"""
    project_id: int = Field(description='所属项目 id（FK→studio_project）')
    render_job_id: int | None = Field(None, description='产出该成品的渲染 job id（FK→studio_render_job；可空兼容外部导入）')
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: str | None = Field(None, description='协作分身 hasn_id（AgentIdentity 展示）')
    title: str = Field(description='成品标题')
    pipeline_key: str | None = Field(None, description='用了哪条管线 key（成品卡展示）')
    video_asset_uri: str = Field(description='成片 mp4 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）')
    thumbnail_asset_uri: str | None = Field(None, description='缩略图/首帧 hasn://asset/')
    duration_sec: Decimal | None = Field(None, description='时长（秒）')
    resolution: str | None = Field(None, description='分辨率（如 1080x1920）')
    status: str = Field(description='状态 (generating:生成中:blue/reviewing:待审核:gold/completed:已完成:green/failed:失败:red)')
    origin_type: str = Field(description='续接归属来源 (app:应用:blue)（AC-P2）')
    active_work_session_id: str | None = Field(None, description='「改一版」回到同一工作会话 id（AC-P2）')
    meta: dict = Field(description='成品元数据 jsonb（provider 用量/成本/分镜数）')


class CreateStudioArtifactParam(StudioArtifactSchemaBase):
    """创建视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数"""


class UpdateStudioArtifactParam(StudioArtifactSchemaBase):
    """更新视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数"""


class DeleteStudioArtifactParam(SchemaBase):
    """删除视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）参数"""

    pks: list[int] = Field(description='视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/） ID 列表')


class GetStudioArtifactDetail(StudioArtifactSchemaBase):
    """视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
