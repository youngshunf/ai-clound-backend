from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class StudioAssetSchemaBase(SchemaBase):
    """视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）基础模型"""
    project_id: int = Field(description='所属项目 id（FK→studio_project）')
    owner_hasn_id: str = Field(description='归属主人 hasn_id（冗余免 join，行级隔离）')
    kind: str = Field(description='素材类型 (script:脚本:blue/image:图片:cyan/audio:音频:purple/video:视频:geekblue/subtitle:字幕:gold/voiceover:配音:magenta/bgm:配乐:lime/font:字体:default)')
    asset_uri: str = Field(description='素材本体 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）')
    source: str = Field(description='素材来源 (upload:主人上传:blue/generated:分身生成:green/stock:库存:cyan/provider:外部provider:orange)')
    title: str | None = Field(None, description='素材显示名')
    meta: dict = Field(description='素材元数据 jsonb（时长/分辨率/语言/采样率）')


class CreateStudioAssetParam(StudioAssetSchemaBase):
    """创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数"""


class UpdateStudioAssetParam(StudioAssetSchemaBase):
    """更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数"""


class DeleteStudioAssetParam(SchemaBase):
    """删除视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数"""

    pks: list[int] = Field(description='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID 列表')


class GetStudioAssetDetail(StudioAssetSchemaBase):
    """视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
