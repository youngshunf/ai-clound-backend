from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateIndustryParam(BaseModel):
    name: str
    parent_id: int | None = None
    keywords: list[str] | None = None
    description: str | None = None
    sort: int = 0


class UpdateIndustryParam(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    keywords: list[str] | None = None
    description: str | None = None
    sort: int | None = None


class IndustrySchema(BaseModel):
    id: int
    name: str
    parent_id: int | None
    keywords: list[str] | None
    description: str | None = None
    sort: int = 0
    children: list['IndustrySchema'] = []

    model_config = ConfigDict(from_attributes=True)


class CreateTopicParam(BaseModel):
    title: str
    industry_id: int
    potential_score: float = 0
    heat_index: float = 0
    reason: str | None = None
    keywords: list[str] | None = None
    platform_heat: dict | None = None
    heat_sources: list[dict] | list[str] | None = None
    trend: dict | list[dict] | None = None
    industry_tags: list[str] | None = None
    target_audience: list[str] | dict | None = None
    creative_angles: list[str] | None = None
    content_outline: list[str] | dict | None = None
    format_suggestions: list[str] | None = None
    material_clues: list[str] | dict | None = None
    risk_notes: list[str] | None = None
    status: int = 0


class UpdateTopicParam(BaseModel):
    title: str | None = None
    industry_id: int | None = None
    potential_score: float | None = None
    heat_index: float | None = None
    reason: str | None = None
    keywords: list[str] | None = None
    platform_heat: dict | None = None
    heat_sources: list[dict] | list[str] | None = None
    trend: dict | list[dict] | None = None
    industry_tags: list[str] | None = None
    target_audience: list[str] | dict | None = None
    creative_angles: list[str] | None = None
    content_outline: list[str] | dict | None = None
    format_suggestions: list[str] | None = None
    material_clues: list[str] | dict | None = None
    risk_notes: list[str] | None = None
    status: int | None = None


class TopicSchema(BaseModel):
    id: int
    title: str
    industry_id: int | None
    potential_score: float
    heat_index: float
    reason: str | None
    keywords: list[str] | None
    platform_heat: dict | None
    heat_sources: list[dict] | list[str] | None
    trend: dict | list[dict] | None
    industry_tags: list[str] | None
    target_audience: list[str] | dict | None
    creative_angles: list[str] | None
    content_outline: list[str] | dict | None
    format_suggestions: list[str] | None
    material_clues: list[str] | dict | None
    risk_notes: list[str] | None
    status: int
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TopicGenerateResponse(BaseModel):
    generated: int
    errors: list[str]
