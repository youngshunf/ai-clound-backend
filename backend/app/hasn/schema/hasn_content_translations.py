from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnContentTranslationsSchemaBase(SchemaBase):
    """用户内容译文缓存（译文是视图，不回写原文表）基础模型"""
    resource_kind: str = Field(description='资源类型 (post:帖子/article:文章/comment:评论/circle:圈子/profile:名片)')
    resource_id: str = Field(description='资源的云端权威 ID（post_id / article_id / comment_id ...）')
    field: str = Field(description='被翻字段 (content:正文/title:标题/summary:摘要)')
    source_lang: str = Field(description='原文语言（检测所得，如 zh / en）')
    target_lang: str = Field(description='目标语言（如 en / ja / zh-TW）')
    source_hash: str = Field(description='原文 sha256；原文改动即自动失效并重译')
    translated_text: str = Field(description='译文正文')
    engine: str = Field(description='翻译引擎/模型名，如 agnes-2.5-flash')
    engine_version: str = Field(description='翻译管线版本；升级 prompt/模型时递增以整体失效')
    token_usage: int = Field(description='本次翻译消耗 token 数，便于事后成本核算')
    hit_count: int = Field(description='缓存命中次数（共享缓存摊薄效果的观测指标）')


class CreateHasnContentTranslationsParam(HasnContentTranslationsSchemaBase):
    """创建用户内容译文缓存（译文是视图，不回写原文表）参数"""


class UpdateHasnContentTranslationsParam(HasnContentTranslationsSchemaBase):
    """更新用户内容译文缓存（译文是视图，不回写原文表）参数"""


class DeleteHasnContentTranslationsParam(SchemaBase):
    """删除用户内容译文缓存（译文是视图，不回写原文表）参数"""

    pks: list[int] = Field(description='用户内容译文缓存（译文是视图，不回写原文表） ID 列表')


class GetHasnContentTranslationsDetail(HasnContentTranslationsSchemaBase):
    """用户内容译文缓存（译文是视图，不回写原文表）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
