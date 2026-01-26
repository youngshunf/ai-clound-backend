"""项目 Schema 定义"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectBase(BaseModel):
    """项目基础字段"""

    name: str = Field(..., min_length=1, max_length=100, description='项目名称')
    description: str | None = Field(None, max_length=500, description='项目描述')

    # 行业领域
    industry: str | None = Field(None, max_length=50, description='行业')
    sub_industries: list[str] = Field(default_factory=list, description='子行业列表')

    # 品牌信息
    brand_name: str | None = Field(None, max_length=100, description='品牌名称')
    brand_tone: str | None = Field(None, max_length=50, description='品牌调性')
    brand_keywords: list[str] = Field(default_factory=list, description='品牌关键词')

    # 内容定位
    topics: list[str] = Field(default_factory=list, description='关注话题')
    keywords: list[str] = Field(default_factory=list, description='内容关键词')
    content_style: str | None = Field(None, max_length=50, description='内容风格')

    # 偏好设置
    account_tags: list[str] = Field(default_factory=list, description='账号标签')
    preferred_platforms: list[str] = Field(default_factory=list, description='偏好平台')


class ProjectCreate(ProjectBase):
    """创建项目请求"""
    uuid: str | None = Field(None, max_length=64, description='项目 UUID')


class ProjectUpdate(BaseModel):
    """更新项目请求"""

    name: str | None = Field(None, min_length=1, max_length=100, description='项目名称')
    description: str | None = Field(None, max_length=500, description='项目描述')

    # 行业领域
    industry: str | None = Field(None, max_length=50, description='行业')
    sub_industries: list[str] | None = Field(None, description='子行业列表')

    # 品牌信息
    brand_name: str | None = Field(None, max_length=100, description='品牌名称')
    brand_tone: str | None = Field(None, max_length=50, description='品牌调性')
    brand_keywords: list[str] | None = Field(None, description='品牌关键词')

    # 内容定位
    topics: list[str] | None = Field(None, description='关注话题')
    keywords: list[str] | None = Field(None, description='内容关键词')
    account_tags: list[str] | None = Field(None, description='账号标签')

    # 偏好设置
    preferred_platforms: list[str] | None = Field(None, description='偏好平台')
    content_style: str | None = Field(None, max_length=50, description='内容风格')


class ProjectResponse(BaseModel):
    """项目响应"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    uuid: str
    description: str | None
    industry: str | None
    sub_industries: list[str]
    brand_name: str | None
    brand_tone: str | None
    brand_keywords: list[str]
    topics: list[str]
    keywords: list[str]
    account_tags: list[str]
    preferred_platforms: list[str]
    content_style: str | None
    is_default: bool
    created_time: datetime
    updated_time: datetime | None


class ProjectListResponse(BaseModel):
    """项目列表响应 (简化版)"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    uuid: str
    industry: str | None
    brand_name: str | None
    is_default: bool
    created_time: datetime
