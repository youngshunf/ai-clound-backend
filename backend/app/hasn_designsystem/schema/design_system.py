from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class DesignSystemSchemaBase(SchemaBase):
    """设计系统（云端权威）基础模型"""
    owner_hasn_id: str = Field(description='归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）')
    name: str = Field(description='设计系统名称')
    slug: str = Field(description='owner 维度唯一 slug（同 owner 不可重复）')
    category: str | None = Field(None, description='分类（可空，如 saas/editorial/playful…）')
    source_kind: str = Field(description='来源 (generated:生成:violet/imported_shadcn:shadcn导入:blue/imported_github:GitHub导入:blue/imported_screenshot:截图导入:cyan/seed:官方内置:green)')
    score: int | None = Field(None, description='当前版评分 0-100（token 契约评分）')
    grade: str | None = Field(None, description='等级 (excellent:优秀:green/good:良好:blue/fair:一般:orange/poor:较差:red)')
    recommend_rebuild: bool = Field(description='是否建议重建（评分过低）')
    is_builtin: bool = Field(description='是否官方内置（seed，跨 owner 只读可见）')
    enterprise_id: int | None = Field(None, description='归属企业 ID（null=个人；非空=企业私有，引用 public.hasn_enterprise）')
    platform_project_id: str | None = Field(None, description='挂靠的平台项目 id（可空=未挂靠）')
    current_revision_id: int | None = Field(None, description='当前版 revision.id（指向最新 revision）')
    content_hash: str = Field(description='当前版内容 hash（供同步 revision diff）')
    required_scenes: list[str] = Field(
        default_factory=lambda: ['brand_website'],
        description='组件画廊要求覆盖的交付物场景 id 列表（brand_website/deck/poster/mobile；默认 [brand_website]）',
    )
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删，不物理删以便同步感知）')


class CreateDesignSystemParam(DesignSystemSchemaBase):
    """创建设计系统（云端权威）参数"""


class UpdateDesignSystemParam(DesignSystemSchemaBase):
    """更新设计系统（云端权威）参数"""


class DeleteDesignSystemParam(SchemaBase):
    """删除设计系统（云端权威）参数"""

    pks: list[int] = Field(description='设计系统（云端权威） ID 列表')


class GetDesignSystemDetail(DesignSystemSchemaBase):
    """设计系统（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
