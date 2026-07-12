"""平台工具 · asset 域（分身按 asset_id 取资产技术元数据，用于校验手里的引用）。

分身拿到 `hasn://asset/{id}`（来自搜索结果、消息附件、历史上下文）后，用 `hasn.asset.get`
确认它是什么、多大、多长，决定怎么用。**只读技术元数据、不返回字节、不做 search**——存储层
（`hasn_assets`）只管「字节在哪、怎么签 URL」，语义检索走 `hasn_artifacts` 层（`hasn.artifact.*`）。

安全（§7）：强制 owner 校验——资产不属于本主人与「不存在」**统一回「资产不存在」**
（不区分无权/不存在，避免探测他人资产 id 空间）。身份恒由 `agent_context` 注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

_TRANSCRIPT_MAX = 2000  # transcript 截断长度（省分身上下文）


def _normalize_asset_id(raw: str) -> str:
    """归一 asset_id：接受裸 `ast_…` 或 `hasn://asset/ast_…`，剥前缀返回裸 id。"""
    value = raw.strip()
    prefix = 'hasn://asset/'
    value = value.removeprefix(prefix)
    return value.strip()


class AssetGetTool(BaseTool):
    """`hasn.asset.get`：按 asset_id 取资产技术元数据（kind/mime/size/宽高/时长/转写）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.asset.get'

    @property
    def namespace(self) -> str:
        return 'hasn.asset'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '按 asset_id 取一个资产的技术元数据（kind/mime/size_bytes/宽高/时长/语音转写），'
            '用于确认手里的 hasn://asset/{id} 引用是什么、多大、多长。'
            '注意：asset 的 kind 只有 image/voice/file 三值——视频资产的 kind 是 file'
            '（video 是 artifact 层的语义类型），按 kind=video 判空会误判。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'asset_id': {
                    'type': 'string',
                    'description': '资产 ID（裸 ast_… 或 hasn://asset/ast_…，工具层归一）',
                },
            },
            'required': ['asset_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        # 读类、身份钉死本主人：不声明独立 capability scope（出厂 Allow）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """按 asset_id 取元数据 + owner 校验（无权/不存在统一报「资产不存在」）。"""
        raw = arguments.get('asset_id')
        if not isinstance(raw, str) or not raw.strip():
            raise RuntimeError("asset.get: 'asset_id' 必填")
        asset_id = _normalize_asset_id(raw)

        async with async_db_session() as db:
            asset = await hasn_asset_service.get_by_asset_id(db, asset_id)

        # owner 校验：不属于本主人与不存在统一回「资产不存在」（不泄露他人资产 id 空间）。
        if asset is None or asset.owner_hasn_id != agent_context.owner_hasn_id:
            raise RuntimeError('asset.get: 资产不存在')

        transcript = asset.transcript
        if isinstance(transcript, str) and len(transcript) > _TRANSCRIPT_MAX:
            transcript = transcript[:_TRANSCRIPT_MAX]

        return {
            'asset_id': asset.asset_id,
            'kind': asset.kind,
            'mime': asset.mime,
            'size_bytes': asset.size_bytes,
            'width': asset.width,
            'height': asset.height,
            'duration_ms': asset.duration_ms,
            'transcript': transcript,
        }


ASSET_TOOLS: list[BaseTool] = [AssetGetTool()]
