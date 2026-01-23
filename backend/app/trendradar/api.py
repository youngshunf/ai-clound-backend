from __future__ import annotations

from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.app.trendradar.service import TrendRadarHotTopicService


router = APIRouter()


class HotTopicsRequest(BaseModel):
    """请求体：按行业 + 关键词获取热点话题。

    该结构会由 agent_core.topic_recommender.providers.trendradar.TrendRadarProvider
    以 payload_mode="trendradar" 的形式发起调用。
    """

    industry: str
    keywords: list[str] = []
    platforms: list[str] | None = None
    limit: int = 50


def get_service() -> TrendRadarHotTopicService:
    # 如有需要可以改为从配置读取 TrendRadar 根路径
    return TrendRadarHotTopicService()


@router.post("/hot-topics")
async def hot_topics(
    body: HotTopicsRequest,
    svc: TrendRadarHotTopicService = Depends(get_service),
) -> dict[str, Any]:
    """返回与 TrendRadarProvider 兼容的热点列表结构。

    响应格式：{"topics": [...]}，其中 topics 为若干热点条目 dict，
    字段命名与 TrendRadarProvider._normalize_response 的解析逻辑兼容。
    """

    try:
        topics = svc.get_hot_topics(
            industry=body.industry,
            keywords=body.keywords,
            platforms=body.platforms,
            limit=body.limit,
        )
        return {"topics": topics}
    except Exception as exc:  # pragma: no cover - 这里只做容错包装
        raise HTTPException(status_code=500, detail=f"TrendRadar adapter error: {exc!s}") from exc
