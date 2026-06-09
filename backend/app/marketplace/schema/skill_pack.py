from datetime import datetime
from typing import Any

from pydantic import Field

from backend.common.schema import SchemaBase


class SkillPackCreateRequest(SchemaBase):
    template_id: str | None = Field(default=None, description='模板 ID；为空时按 bundle_slug 生成')
    namespace: str | None = Field(default=None, description='命名空间')
    name: str = Field(description='技能包名称（落库 marketplace_template.name；hub 同步时取 bundle.yaml 的中文 display_name，缺省回退 slug）')
    description: str | None = Field(default=None, description='技能包描述')
    icon_url: str | None = Field(default=None, description='图标 URL（hub 同步上传 icon.svg 到公共桶后回填；留空则不覆盖现值）')
    category: str | None = Field(default=None, description='分类（与 marketplace_category 共用）')
    bundle_slug: str = Field(description='skill pack slug')
    command_key: str = Field(description='Hermes 命令 key')
    version: str = Field(default='1.0.0', description='语义化版本')
    hermes_bundle_json: dict[str, Any] = Field(default_factory=dict, description='Hermes bundle JSON')
    hermes_yaml: str = Field(description='Hermes YAML')
    content_hash: str | None = Field(default=None, description='内容哈希（留空时由 hermes_yaml 规范化后计算）')
    skill_dependencies_versioned: dict[str, Any] | None = Field(default=None, description='带版本号的技能依赖')
    is_private: bool = Field(default=True, description='是否私有')
    is_official: bool = Field(default=False, description='是否官方')
    # webui 创建走草稿态（status='draft'）→ 进「我的发布」可提审/发布；
    # 留空时保持 DB 列默认（'published'），供 hub 同步 / MCP publish 直接发布。
    status: str | None = Field(default=None, description='发布状态（draft/pending_review/published/...）；留空保持默认')


class SkillPackResponse(SchemaBase):
    template_id: str
    version: str
    name: str
    description: str | None = None
    bundle_slug: str
    command_key: str
    hermes_bundle_json: dict[str, Any] | None = None
    hermes_yaml: str
    content_hash: str
    package_url: str | None = None
    file_hash: str | None = None
    published_at: datetime | None = None
    # 卡片渲染字段（与技能/模板卡片对齐，供 webui ResourceCard 复用）。
    namespace: str | None = None
    slug: str | None = None
    icon_url: str | None = None
    emoji: str | None = None
    category: str | None = None
    tags: str | None = None
    source_type: str | None = None
    is_official: bool | None = None
    download_count: int | None = None
    status: str | None = None
    visibility: str | None = None
    author_name: str | None = None


class SkillPackPage(SchemaBase):
    """分页信封（与技能/模板浏览 MarketplacePage 对齐）。"""

    items: list[SkillPackResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
