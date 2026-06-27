from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

from backend.app.hasn_growth.service.maps_place_client import poi_to_structured, search_place_pois
from backend.app.hasn_growth.service.scrapy_crawler_client import crawl_leads, scrapy_item_to_structured
from backend.common.log import log
from backend.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

# ② URL 去重 / ④ 精确配额：搜索候选数与单源抓取的硬上限，防 search 返回过多失控烧成本（doc08 §4.4/§7）
_SEARCH_LIMIT_CAP = 50
_CRAWL_HARD_CAP = 50


@dataclass(slots=True)
class CrawlRequest:
    job_id: int
    keyword: str
    source_type: str
    lead_scope: str
    user_id: int | None = None
    max_pages: int = 5
    max_results: int = 100
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CrawledItem:
    source_type: str
    source_url: str | None = None
    title: str | None = None
    markdown: str | None = None
    raw_text: str | None = None
    raw_html: str | None = None
    raw_payload: dict[str, Any] | None = None
    structured_payload: dict[str, Any] | None = None
    llm_confidence: float | None = None
    extract_mode: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    firecrawl_request_id: int | None = None


class FirecrawlLike(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]: ...
    async def scrape_markdown(self, url: str) -> dict[str, Any]: ...
    async def scrape_lead_json(self, url: str, schema_version: str, prompt_version: str) -> dict[str, Any]: ...
    async def extract_leads(self, urls: list[str], schema_version: str, prompt_version: str) -> dict[str, Any]: ...


class LeadLLMExtractorLike(Protocol):
    async def extract(self, markdown: str | None, *, source_url: str | None = None) -> dict[str, Any] | None: ...


class UrlDedupLike(Protocol):
    async def filter_unseen(self, urls: list[str]) -> list[str]: ...


class BaseProvider:
    source_type = ''

    async def crawl_stream(
        self,
        request: CrawlRequest,
        *,
        firecrawl_client: FirecrawlLike,
        llm_extractor: LeadLLMExtractorLike | None = None,
        dedup: UrlDedupLike | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> AsyncIterator[CrawledItem]:
        """④ 真流式收口：逐条抓取，每抓**下一条前**查 ``should_continue()``，False 即停（doc08 §7 / doc93 2.4）。

        调用方（business_service.run_job）传 ``lambda: job.valid_count < job.max_results``——
        凑够目标有效线索即停止抓取剩余候选 URL，**省 firecrawl/LLM 成本，补爬精确到条零冗余**。
        ``should_continue=None`` 时退化为抓全部候选（``crawl`` 列表包装即走此路径，向后兼容）。
        """
        options = request.config.get('firecrawl_options', {})
        schema_version = options.get('schema_version', 'lead_v1')
        prompt_version = options.get('prompt_version', 'lead_extract_v1')
        urls = await self._resolve_urls(request, firecrawl_client=firecrawl_client)
        if dedup is not None:
            # ② URL 级去重：抓取前剔除近期已成功抓过的 URL（登记由 business 落库时做，避免重复计数）
            urls = await dedup.filter_unseen(urls)
        for url in urls[:_CRAWL_HARD_CAP]:
            if should_continue is not None and not should_continue():
                break  # ④ 调用方已凑够目标 → 不再抓后续 URL（真流式收口·省成本）
            if options.get('extract_mode') == 'extract':
                # 兼容路径：firecrawl 原生 extract（仅当 firecrawl 自身配了可用 LLM 时有效）
                result = await firecrawl_client.extract_leads([url], schema_version, prompt_version)
            else:
                # 方案 A 主路径：firecrawl 只抓 markdown，结构化提取下沉到后端 LLM（doc08 §3）
                result = await firecrawl_client.scrape_markdown(url)
                await self._apply_backend_llm(result, url, llm_extractor)
            yield self._result_to_item(result, fallback_url=url)

    async def crawl(
        self,
        request: CrawlRequest,
        *,
        firecrawl_client: FirecrawlLike,
        llm_extractor: LeadLLMExtractorLike | None = None,
        dedup: UrlDedupLike | None = None,
    ) -> list[CrawledItem]:
        """一次性抓全部候选（向后兼容包装；可中断流式走 ``crawl_stream``）。"""
        return [
            item
            async for item in self.crawl_stream(
                request, firecrawl_client=firecrawl_client, llm_extractor=llm_extractor, dedup=dedup
            )
        ]

    @staticmethod
    async def _apply_backend_llm(
        result: dict[str, Any], url: str, llm_extractor: LeadLLMExtractorLike | None
    ) -> None:
        """firecrawl 未返结构化时，用 markdown 调后端 LLM 补 structured_payload；失败静默退正则兜底。"""
        if llm_extractor is None or result.get('structured_payload') or not result.get('markdown'):
            return
        extracted = await llm_extractor.extract(result['markdown'], source_url=result.get('source_url') or url)
        if not extracted or not extracted.get('structured_payload'):
            return
        result['structured_payload'] = extracted['structured_payload']
        result['llm_confidence'] = extracted.get('llm_confidence')
        result['extract_mode'] = 'llm_backend'
        result['llm_schema_version'] = extracted.get('schema_version')
        result['llm_prompt_version'] = extracted.get('prompt_version')

    async def _resolve_urls(self, request: CrawlRequest, *, firecrawl_client: FirecrawlLike) -> list[str]:
        if self._is_url_like(request.keyword):
            return [self._keyword_to_url(request.keyword)]
        options = request.config.get('firecrawl_options', {})
        search_limit = int(options.get('search_limit') or min(max(request.max_results, 1), 10))
        results = await firecrawl_client.search(request.keyword, limit=search_limit)
        return [item['url'] for item in results if item.get('url')]

    def _result_to_item(self, result: dict[str, Any], *, fallback_url: str) -> CrawledItem:
        return CrawledItem(
            source_type=self.source_type,
            source_url=result.get('source_url') or fallback_url,
            title=result.get('title'),
            markdown=result.get('markdown'),
            raw_text=result.get('raw_text'),
            raw_html=result.get('raw_html'),
            raw_payload=result.get('raw_payload') or result,
            structured_payload=result.get('structured_payload'),
            llm_confidence=result.get('llm_confidence'),
            extract_mode=result.get('extract_mode'),
            metadata={
                'llm_schema_version': result.get('llm_schema_version'),
                'llm_prompt_version': result.get('llm_prompt_version'),
                'attempt_count': result.get('attempt_count'),
            },
        )

    def _is_url_like(self, keyword: str) -> bool:
        if keyword.startswith(('http://', 'https://')):
            return True
        parsed = urlparse(f'https://{keyword.strip("/")}')
        return bool(parsed.netloc and '.' in parsed.netloc and not any(char.isspace() for char in keyword))

    def _keyword_to_url(self, keyword: str) -> str:
        if keyword.startswith(('http://', 'https://')):
            return keyword
        return f'https://{keyword.strip("/")}'


PROVIDERS: dict[str, BaseProvider] = {}


def register(source_type: str):
    def decorator(cls: type[BaseProvider]) -> type[BaseProvider]:
        instance = cls()
        instance.source_type = source_type
        PROVIDERS[source_type] = instance
        return cls

    return decorator


def get_provider(source_type: str) -> BaseProvider:
    return PROVIDERS[source_type]


@register('maps')
class MapsProvider(BaseProvider):
    """地图 POI 源（doc93 §3.2 混合架构）：直连高德/百度 Place API 出 POI，**跳过 firecrawl + LLM**。

    POI 本就结构化（公司名/地址/电话/行政区）→ 预置 ``structured_payload``，下游 cleaner_service
    直接落库（与 LLM 提取产物同键）。``city`` 取自任务 ``config.city/region``；未配 key 则诚实跳过
    （零 fake·真实 key 由运营配置·真抓 E2E infra-gated）。
    """

    async def crawl_stream(
        self,
        request: CrawlRequest,
        *,
        firecrawl_client: FirecrawlLike,
        llm_extractor: LeadLLMExtractorLike | None = None,
        dedup: UrlDedupLike | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> AsyncIterator[CrawledItem]:
        amap_key = (settings.AMAP_API_KEY or '').strip()
        baidu_ak = (settings.BAIDU_MAP_AK or '').strip()
        if not amap_key and not baidu_ak:
            log.warning(
                '[MapsProvider] 未配 AMAP_API_KEY / BAIDU_MAP_AK，maps 源跳过'
                '（doc93 §3.2·真实 key 由运营配置·infra-gated）'
            )
            return  # 空生成器：诚实不出数，不 fake
        cfg = request.config or {}
        city = str(cfg.get('city') or cfg.get('region') or '').strip()
        limit = min(max(request.max_results, 1), _CRAWL_HARD_CAP)
        pois = await search_place_pois(
            keyword=request.keyword, city=city, limit=limit, amap_key=amap_key, baidu_ak=baidu_ak
        )
        emitted = 0
        for poi in pois:
            if should_continue is not None and not should_continue():
                break  # ④ 真流式收口：调用方已凑够目标 → 不再产出后续 POI（与 firecrawl 路径一致）
            structured = poi_to_structured(poi)
            yield CrawledItem(
                source_type=self.source_type,
                source_url=f'maps://{poi.get("source", "amap")}/{structured["company_name"]}',
                title=structured['company_name'],
                structured_payload=structured,
                llm_confidence=0.9,  # POI 来自权威 Place API（已结构化），高可信
                extract_mode='maps_poi',
                raw_payload=poi,
                metadata={'maps_source': poi.get('source'), 'location': poi.get('location')},
            )
            emitted += 1
            if emitted >= limit:
                break


@register('yellow_pages')
@register('b2b')
class ScrapyProvider(BaseProvider):
    """黄页/B2B 深爬源（doc93 §3.1 混合架构）：cloud-brokered 调 lead-crawler-service 出详情页线索。

    Scrapy 有独立 Twisted reactor 与 FastAPI async 冲突 → 抓取下沉到独立服务，本 provider 仅中转
    （**daemon 不反代**）。服务返回**已结构化** items（spider 列表分页→详情页深爬）→ 预置
    ``structured_payload`` 跳过 LLM。服务未配/不可达 → 诚实产空（零 fake·真实部署 E2E infra-gated）。
    一个类经叠加装饰器同时承载 ``yellow_pages`` 与 ``b2b`` 两个 source_type（spider 规则由服务侧分流）。
    """

    async def crawl_stream(
        self,
        request: CrawlRequest,
        *,
        firecrawl_client: FirecrawlLike,
        llm_extractor: LeadLLMExtractorLike | None = None,
        dedup: UrlDedupLike | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> AsyncIterator[CrawledItem]:
        limit = min(max(request.max_results, 1), _CRAWL_HARD_CAP)
        result = await crawl_leads(
            source_type=self.source_type,
            keyword=request.keyword,
            max_results=limit,
            options=request.config.get('crawler_options') if isinstance(request.config, dict) else None,
        )
        if not result.get('ok'):
            log.warning(
                '[ScrapyProvider] %s 深爬未出数（%s）：%s',
                self.source_type,
                result.get('error'),
                result.get('message'),
            )
            return  # 空生成器：诚实不出数，不 fake
        emitted = 0
        for item in result.get('items') or []:
            if not isinstance(item, dict):
                continue
            if should_continue is not None and not should_continue():
                break  # ④ 真流式收口：调用方已凑够目标 → 不再产出后续 item
            structured = scrapy_item_to_structured(item)
            if not structured['company_name']:
                continue  # 无主体名的 item 丢弃（与各 parser 一致）
            yield CrawledItem(
                source_type=self.source_type,
                source_url=str(item.get('source_url') or item.get('url') or '') or None,
                title=str(item.get('title') or structured['company_name']) or None,
                structured_payload=structured,
                llm_confidence=0.85,  # spider 定向抽取，较 LLM 提取更稳定
                extract_mode='scrapy',
                raw_payload=item,
            )
            emitted += 1
            if emitted >= limit:
                break


@register('social_media')
class SocialMediaProvider(BaseProvider):
    pass


@register('public_web')
class PublicWebProvider(BaseProvider):
    pass
