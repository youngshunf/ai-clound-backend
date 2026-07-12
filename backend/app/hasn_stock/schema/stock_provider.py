"""素材站目录 admin 读写 DTO（A-P2-0）。

api_key **明文只进不出**：写入走 `api_key`（明文，加密落库），读出只回 `api_key_configured`（是否已配）
+ `api_key_masked`（掩码），**绝不回显明文**（对齐 external_mcp secret / newapi APIKey）。
"""

from datetime import datetime

from pydantic import Field

from backend.common.schema import SchemaBase

# 媒体类型合法值
MEDIA_TYPES = ('image', 'video')


class StockProviderItem(SchemaBase):
    """素材站目录项（admin 列表/详情；api_key 掩码不回显明文）。"""

    id: int
    provider: str
    display_name: str
    media_types: list[str] = Field(default_factory=list)
    api_key_configured: bool = Field(description='是否已配 api_key（不回显明文）')
    api_key_masked: str | None = Field(None, description='api_key 掩码（如 ****abcd；仅展示尾 4 位）')
    download_domains: list[str] = Field(default_factory=list)
    enabled: bool
    priority: int
    license_terms_url: str | None = None
    remark: str | None = None
    created_time: datetime
    updated_time: datetime | None = None


class CreateProviderParam(SchemaBase):
    """新增素材站（admin）。"""

    provider: str = Field(min_length=1, max_length=40, description='唯一标识（pexels/pixabay/coverr/…）')
    display_name: str = Field('', max_length=80)
    media_types: list[str] = Field(default_factory=list, description='支持的媒体类型（image/video 子集）')
    api_key: str | None = Field(None, description='明文 api_key（加密落库；不回显）')
    download_domains: list[str] = Field(default_factory=list, description='下载直链合法域名')
    enabled: bool = True
    priority: int = 100
    license_terms_url: str | None = Field(None, max_length=500)
    remark: str | None = Field(None, max_length=255)


class UpdateProviderParam(SchemaBase):
    """更新素材站（admin）。所有字段可选；`api_key` 传空串=清空 key，传 None=不改。"""

    display_name: str | None = Field(None, max_length=80)
    media_types: list[str] | None = None
    api_key: str | None = Field(None, description='明文 api_key（传空串清空 key，None 不改）')
    download_domains: list[str] | None = None
    enabled: bool | None = None
    priority: int | None = None
    license_terms_url: str | None = Field(None, max_length=500)
    remark: str | None = Field(None, max_length=255)
