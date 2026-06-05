from datetime import datetime
from typing import Any

from pydantic import Field

from backend.common.schema import SchemaBase


class SkillPackCreateRequest(SchemaBase):
    template_id: str | None = Field(default=None, description='模板 ID；为空时按 bundle_slug 生成')
    namespace: str | None = Field(default=None, description='命名空间')
    name: str = Field(description='技能包名称')
    description: str | None = Field(default=None, description='技能包描述')
    bundle_slug: str = Field(description='skill pack slug')
    command_key: str = Field(description='Hermes 命令 key')
    version: str = Field(default='1.0.0', description='语义化版本')
    hermes_bundle_json: dict[str, Any] = Field(default_factory=dict, description='Hermes bundle JSON')
    hermes_yaml: str = Field(description='Hermes YAML')
    content_hash: str | None = Field(default=None, description='内容哈希（留空时由 hermes_yaml 规范化后计算）')
    skill_dependencies_versioned: dict[str, Any] | None = Field(default=None, description='带版本号的技能依赖')
    is_private: bool = Field(default=True, description='是否私有')
    is_official: bool = Field(default=False, description='是否官方')


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
