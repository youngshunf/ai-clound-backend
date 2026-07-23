"""平台工具 · stock 域（通用素材站搜索与下载，A-P2）。

- `hasn.stock.search`：搜外部素材站（图片 + 视频）。`source` 可选点名，不传走默认 failover 链；
  `source` enum 由 provider 目录进程内缓存动态渲染（TTL 60s），execute 以 DB 为权威复校。
- `hasn.stock.download`：把选中素材下载进 owner 私有桶 + 双登记（asset + artifact），下载后即可被
  `hasn.artifact.search` 搜回。SSRF 白名单由 provider 目录 `download_domains` 并集驱动。

身份恒由 `agent_context` 注入；`session_id` 取系统注入的 `_hasn_session_id`（work_session_id）。
不外发、不动钱 → `required_scopes=[]`（出厂 Allow）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_stock.service.download_service import stock_download_service
from backend.app.hasn_stock.service.provider_store import stock_provider_store
from backend.app.hasn_stock.service.stock_service import stock_service
from backend.app.mcp.tools.base import BaseTool

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

_PER_PAGE_DEFAULT = 10
_PER_PAGE_MAX = 20


def _require_owner_hasn_id(agent_context: AgentContext) -> str:
    """从已鉴权分身上下文取得主人身份，缺失时拒绝写入主人私有资源。"""
    owner_hasn_id = agent_context.owner_hasn_id
    if not owner_hasn_id:
        raise RuntimeError('stock: Agent 凭证缺少 owner_hasn_id')
    return owner_hasn_id


class StockSearchTool(BaseTool):
    """`hasn.stock.search`：搜外部素材站（图片 + 视频）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.stock.search'

    @property
    def namespace(self) -> str:
        return 'hasn.stock'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        sources = stock_provider_store.cached_source_enum()
        return (
            '搜外部素材站（图片 + 视频），返回候选直链供挑选。'
            f'当前可选素材站：{sources}（不传 source 走默认 failover 链，按优先级逐站尝试）。'
            '每条候选带一个 description（素材语义描述，来自 pexels alt / pixabay tags / coverr 简介）——'
            '选中后下载时把它原样回传给 hasn.stock.download 的 description 参数，能让这张素材日后被 '
            'hasn.artifact.search 按语义关键词搜回。'
            '⚠️ 出参的 source_url 是外站直链，**禁止**直接写进正文或对外发布（会被防盗链/随时失效）；'
            '选中素材务必先用 hasn.stock.download 收进私有桶，正文只用它返回的 hasn://asset/{id}。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        # source enum 动态渲染：读 provider 目录进程内缓存（TTL 60s，best-effort）。execute 以 DB 复校。
        sources = stock_provider_store.cached_source_enum()
        return {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '搜索关键词'},
                'media_type': {
                    'type': 'string',
                    'enum': ['image', 'video'],
                    'description': '媒体类型（默认 image）',
                },
                'source': {
                    'type': 'string',
                    'enum': sources,
                    'description': '点名素材站（可选）；不传走默认 failover 链。点名后失败直接报错，不偷换',
                },
                'orientation': {
                    'type': 'string',
                    'enum': ['landscape', 'portrait', 'square'],
                    'description': '朝向（可选）',
                },
                'per_page': {
                    'type': 'integer',
                    'description': f'返回条数（默认 {_PER_PAGE_DEFAULT}，封顶 {_PER_PAGE_MAX}）',
                },
            },
            'required': ['query'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """搜素材站，返回候选列表（每条带 provider 标注实际来源站）。"""
        results = await stock_service.search(
            query=arguments.get('query', ''),
            media_type=arguments.get('media_type') or 'image',
            source=(arguments.get('source') or None),
            orientation=(arguments.get('orientation') or None),
            per_page=arguments.get('per_page'),
        )
        return {'results': results, 'count': len(results)}


class StockDownloadTool(BaseTool):
    """`hasn.stock.download`：下载素材站资源 → owner 私有桶 → 双登记（asset + artifact）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.stock.download'

    @property
    def namespace(self) -> str:
        return 'hasn.stock'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '把 hasn.stock.search 选中的素材（source_url）下载进你主人的私有桶，返回可安全引用的 '
            'hasn://asset/{id}，并自动登记进产物库（此后可被 hasn.artifact.search 搜回、可在正文用 '
            '![](hasn://asset/{id}) 嵌图）。只接受素材站白名单内的 https 直链，不是通用下载器。'
            '⭐ 强烈建议把该素材在 search 出参里的 description 原样回传给 description 参数——它会作为'
            '产物的语义摘要落库，让这张素材日后能被「日落/城市/花卉」等关键词搜回（否则只能靠文件名搜，'
            '而素材文件名往往是 pexels-photo-123.jpg 这种无意义串）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'url': {'type': 'string', 'description': '素材直链（取自 hasn.stock.search 出参的 source_url）'},
                'title': {'type': 'string', 'description': '展示标题（可选）'},
                'description': {
                    'type': 'string',
                    'description': (
                        '素材语义描述（可选但强烈建议）：把该素材在 hasn.stock.search 出参里的 '
                        'description 字段原样传入，会落进产物 summary，显著提升日后 hasn.artifact.search 的召回。'
                    ),
                },
            },
            'required': ['url'],
        }

    @property
    def required_scopes(self) -> list[str]:
        # 不外发、不动钱 → 出厂 Allow；资源滥用由大小封顶 + 存储配额兜底。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """SSRF 闸 → 流式下载封顶 → 落私有桶 → 双登记。"""
        owner_hasn_id = _require_owner_hasn_id(agent_context)
        raw = arguments.get('url')
        if not isinstance(raw, str) or not raw.strip():
            raise RuntimeError("stock.download: 'url' 必填")
        return await stock_download_service.download(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_context.agent_hasn_id,
            url=raw.strip(),
            title=(arguments.get('title') or None),
            description=(arguments.get('description') or None),
            session_id=agent_context.session_id,
        )


STOCK_TOOLS: list[BaseTool] = [StockSearchTool(), StockDownloadTool()]
