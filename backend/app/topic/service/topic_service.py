import hashlib
import random

from collections.abc import Sequence
from datetime import date

from agent_core.llm.direct_client import DirectLLMClient
from agent_core.llm.interface import LLMConfig
from agent_core.topic_recommender import TopicRecommender
from agent_core.topic_recommender.analyzers.llm_topic_card import LLMTopicCardAnalyzer
from agent_core.topic_recommender.providers.trendradar import TrendRadarProvider
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.core.gateway import llm_gateway
from backend.app.topic.model.topic import Topic
from backend.app.topic.schema.topic import CreateTopicParam, UpdateTopicParam
from backend.app.topic.service.industry_service import IndustryService
from backend.common.exception import errors
from backend.core.conf import settings


class TopicService:
    @staticmethod
    async def get(db: AsyncSession, id: int) -> Topic | None:
        """获取单个选题"""
        return await db.get(Topic, id)

    @staticmethod
    async def create(db: AsyncSession, obj: CreateTopicParam) -> Topic:
        """创建选题"""
        topic = Topic(**obj.model_dump())
        db.add(topic)
        await db.commit()
        await db.refresh(topic)
        return topic

    @staticmethod
    async def update(db: AsyncSession, id: int, obj: UpdateTopicParam) -> Topic:
        """更新选题"""
        topic = await TopicService.get(db, id)
        if not topic:
            raise errors.NotFoundError(msg='选题不存在')

        update_data = obj.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(topic, key, value)

        await db.commit()
        await db.refresh(topic)
        return topic

    @staticmethod
    async def delete(db: AsyncSession, id: int) -> None:
        """删除选题"""
        topic = await TopicService.get(db, id)
        if not topic:
            raise errors.NotFoundError(msg='选题不存在')

        await db.delete(topic)
        await db.commit()

    @staticmethod
    async def get_recommendations(
        db: AsyncSession,
        industry_id: int | None = None,
        limit: int = 20
    ) -> Sequence[Topic]:
        """获取推荐选题列表"""
        stmt = select(Topic).where(Topic.status == 0)

        if industry_id:
            stmt = stmt.where(Topic.industry_id == industry_id)

        stmt = stmt.order_by(desc(Topic.potential_score)).limit(limit)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def generate_daily_topics(db: AsyncSession) -> dict:
        """生成每日选题（核心任务逻辑）"""
        industries = await IndustryService.get_all_active_industries(db)

        generated_count = 0
        errors = []

        llm_config = LLMConfig(
            base_url="",
            api_token="",
            default_model=settings.LLM_TOPIC_RECOMMENDER_MODEL,
        )
        llm_client = DirectLLMClient(llm_gateway, llm_config)
        analyzer = LLMTopicCardAnalyzer(
            client=llm_client,
            model=settings.LLM_TOPIC_RECOMMENDER_MODEL,
        )

        base_url = settings.TRENDRADAR_BASE_URL.strip() or settings.BETTAFISH_API_URL
        endpoint_path = settings.TRENDRADAR_ENDPOINT_PATH
        payload_mode = settings.TRENDRADAR_PAYLOAD_MODE
        if not settings.TRENDRADAR_BASE_URL.strip():
            endpoint_path = "/api/insight/hot"
            payload_mode = "bettafish_compat"

        provider = TrendRadarProvider(
            base_url=base_url,
            endpoint_path=endpoint_path,
            payload_mode=payload_mode,
        )
        recommender = TopicRecommender(provider=provider, analyzer=analyzer)

        today = date.today()
        for industry in industries:
            try:
                keywords = industry.keywords or [industry.name]

                await db.execute(
                    delete(Topic).where(
                        Topic.industry_id == industry.id,
                        Topic.status == 0,
                        Topic.batch_date == today,
                    )
                )

                cards = await recommender.run(
                    industry_name=industry.name,
                    keywords=keywords,
                    hot_limit=50,
                    count=5,
                )

                if not cards:
                    mock_hot_topics = _generate_mock_hot_topics(industry.name)
                    for hot_topic in mock_hot_topics:
                        platforms = hot_topic.get("platforms") or {}
                        title = f"{hot_topic.get('topic', industry.name)}：{_generate_title_suffix(industry.name)}"
                        source_uid = hashlib.sha256(
                            f"{industry.id}:{today.isoformat()}:{title}".encode()
                        ).hexdigest()
                        topic = Topic(
                            title=title,
                            industry_id=industry.id,
                            potential_score=float(hot_topic.get("heat_score") or 0.0),
                            heat_index=float(hot_topic.get("heat_score") or 0.0),
                            reason=str(hot_topic.get("insight") or ""),
                            keywords=list(hot_topic.get("tags") or []),
                            platform_heat=dict(platforms),
                            heat_sources=list(hot_topic.get("heat_sources") or []),
                            trend=dict(hot_topic.get("trend") or {}),
                            industry_tags=list(hot_topic.get("industry_tags") or [industry.name]),
                            target_audience=list(hot_topic.get("target_audience") or []),
                            creative_angles=list(hot_topic.get("creative_angles") or []),
                            content_outline=list(hot_topic.get("content_outline") or []),
                            format_suggestions=list(hot_topic.get("format_suggestions") or []),
                            material_clues=list(hot_topic.get("material_clues") or []),
                            risk_notes=list(hot_topic.get("risk_notes") or []),
                            source_info=dict(hot_topic),
                            batch_date=today,
                            source_uid=source_uid,
                            status=0,
                        )
                        db.add(topic)
                        generated_count += 1
                    continue

                for card in cards:
                    card.ensure_defaults()
                    if not card.industry_tags:
                        card.industry_tags = [industry.name]
                    card.source_info.setdefault("meta", {})
                    card.source_info["meta"].update(
                        {
                            "industry": industry.name,
                            "keywords": keywords,
                            "provider": "trendradar_local" if settings.TRENDRADAR_BASE_URL.strip() else "bettafish",
                            "provider_base_url": base_url,
                            "provider_endpoint": endpoint_path,
                        }
                    )
                    source_uid = hashlib.sha256(
                        f"{industry.id}:{today.isoformat()}:{card.title}".encode()
                    ).hexdigest()

                    topic = Topic(
                        title=card.title,
                        industry_id=industry.id,
                        potential_score=float(card.potential_score),
                        heat_index=float(card.heat_index),
                        reason=str(card.reason),
                        keywords=list(card.keywords),
                        platform_heat=dict(card.platform_heat),
                        heat_sources=list(card.heat_sources),
                        trend=dict(card.trend),
                        industry_tags=list(card.industry_tags),
                        target_audience=card.target_audience,
                        creative_angles=list(card.creative_angles),
                        content_outline=card.content_outline,
                        format_suggestions=list(card.format_suggestions),
                        material_clues=card.material_clues,
                        risk_notes=list(card.risk_notes),
                        source_info=dict(card.source_info),
                        batch_date=today,
                        source_uid=source_uid,
                        status=0,
                    )
                    db.add(topic)
                    generated_count += 1

            except Exception as e:
                errors.append(f"Error processing industry {industry.name}: {e!s}")

        await db.commit()
        return {"generated": generated_count, "errors": errors}


def _generate_mock_hot_topics(industry_name: str) -> list[dict]:
    """生成模拟热点数据（当 API 不可用时）"""
    base_topics = {
        "人工智能": ["ChatGPT新功能发布", "Midjourney V6评测", "AI绘画变现指南"],
        "数码产品": ["iPhone 16爆料", "华为Mate 70上手", "高性价比千元机推荐"],
        "彩妆": ["早八伪素颜妆", "清冷感眼妆教程", "国货彩妆黑马盘点"],
        "护肤": ["早C晚A正确用法", "敏感肌换季护肤", "祛痘印实测"],
        "食谱教程": ["空气炸锅懒人美食", "一周减脂餐打卡", "零失败戚风蛋糕"],
        "穿搭": ["初春穿搭趋势", "梨形身材穿搭", "法式复古风"],
    }

    topics = base_topics.get(industry_name, [f"{industry_name}行业新趋势", f"{industry_name}避坑指南", f"{industry_name}入门必看"])

    results = []
    for t in topics:
        score = random.randint(70, 95)
        results.append({
            "topic": t,
            "heat_score": score,
            "insight": f"结合当前{industry_name}热点，该选题具有极高的爆款潜力，建议结合具体案例进行分析。",
            "tags": [industry_name, "热点", "趋势"],
            "platforms": {
                "小红书": score + random.randint(-5, 5),
                "抖音": score + random.randint(-10, 0),
                "B站": score + random.randint(-15, -5)
            },
            "heat_sources": [
                {"platform": "小红书", "type": "平台", "score": score + random.randint(-5, 5)},
                {"platform": "抖音", "type": "平台", "score": score + random.randint(-10, 0)},
            ],
            "trend": {
                "direction": "up",
                "window": "7d",
                "score": score,
            },
            "industry_tags": [industry_name],
            "target_audience": ["新手", "进阶用户"],
            "creative_angles": ["对比评测", "避坑清单", "场景化实测"],
            "content_outline": ["开头痛点", "核心方法", "案例拆解", "总结行动点"],
            "format_suggestions": ["图文", "短视频"],
            "material_clues": ["用户评论区高频问题", "平台热搜话题"],
            "risk_notes": ["避免夸大效果", "注意平台合规用语"]
        })
    return results


def _generate_title_suffix(industry_name: str) -> str:
    suffixes = [
        "新手必看指南",
        "深度解析与实操",
        "如何抓住这波红利？",
        "千万别踩这些坑",
        "保姆级教程来了"
    ]
    return random.choice(suffixes)
