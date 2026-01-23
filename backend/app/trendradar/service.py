from __future__ import annotations

from typing import Any, Iterable

try:  # Optional dependency: external/TrendRadar package
    from mcp_server.services.data_service import DataService
except ModuleNotFoundError:  # pragma: no cover - handled at runtime in __init__
    DataService = None  # type: ignore

from backend.core.conf import settings
from backend.core.path_conf import BASE_PATH


def _default_trendradar_root() -> str:
    """推断 TrendRadar 项目根路径。

    优先使用配置 TRENDRADAR_PROJECT_ROOT；
    若为空，则基于 BASE_PATH 计算仓库根目录，再拼接 external/TrendRadar。
    """

    if settings.TRENDRADAR_PROJECT_ROOT:
        return settings.TRENDRADAR_PROJECT_ROOT

    # BASE_PATH = .../services/cloud-backend/backend
    # 仓库根目录 = BASE_PATH.parent.parent.parent
    repo_root = BASE_PATH.parent.parent.parent
    return str(repo_root / "external" / "TrendRadar")


class TrendRadarHotTopicService:
    """读取本地 TrendRadar 数据，并按行业 + 关键词输出热点条目列表。

    该服务仅负责从 TrendRadar 已经抓取好的数据中做过滤和归一化，不触发爬虫。
    """

    def __init__(self, project_root: str | None = None) -> None:
        if DataService is None:
            # 明确提示需要安装 external/TrendRadar 到当前环境
            raise RuntimeError(
                "TrendRadarHotTopicService requires the external/TrendRadar package. "
                "Please install it into the cloud-backend environment, e.g. "
                "`pip install -e ../../external/TrendRadar` from services/cloud-backend."
            )

        self.project_root = project_root or _default_trendradar_root()
        # DataService 会从 TrendRadar 的 output/news 等目录读取当天数据
        self._data_service = DataService(project_root=self.project_root)

    def get_hot_topics(
        self,
        *,
        industry: str,
        keywords: Iterable[str] | None = None,
        platforms: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按行业 + 关键词从 TrendRadar 最新数据中筛选热点条目。

        返回结构与 agent_core.topic_recommender.providers.trendradar.TrendRadarProvider
        的 _normalize_response 能够解析的 JSON 兼容。
        """

        kw_list = [k.strip() for k in (keywords or []) if k and k.strip()]
        if not kw_list:
            kw_list = [industry]

        # 1. 读取 TrendRadar 最新一批新闻（全量）
        news_list = self._data_service.get_latest_news(
            platforms=platforms,
            limit=1000,  # 先取较大上限，后续再按关键词和 limit 截断
            include_url=True,
        )

        # 2. 根据关键词过滤
        matched: list[dict[str, Any]] = []
        for item in news_list:
            title = item.get("title") or ""
            if not isinstance(title, str):
                continue

            lower_title = title.lower()
            if any(k.lower() in lower_title for k in kw_list):
                matched.append(item)

        # 3. 归一化为“热点条目”结构
        topics: list[dict[str, Any]] = []
        for item in matched[:limit]:
            rank = int(item.get("rank") or 0)
            # 越靠前热度越高的简单归一化：1-100 之间
            score = max(1, 100 - rank) if rank > 0 else 50

            platform_name = (
                item.get("platform_name")
                or item.get("platform")
                or "unknown"
            )

            topic_title = item.get("title") or ""
            url = item.get("url") or item.get("mobileUrl") or ""

            topics.append(
                {
                    "topic": topic_title,
                    "heat_score": score,
                    "tags": kw_list,
                    "platforms": {str(platform_name): score},
                    "heat_sources": [
                        {
                            "platform": platform_name,
                            "type": "platform",
                            "score": score,
                        }
                    ],
                    "trend": {
                        "direction": "unknown",
                        "window": "1d",
                        "score": score,
                    },
                    "industry_tags": [industry],
                    "summary": "",
                    "url": url,
                    "raw": item,
                }
            )

        return topics
