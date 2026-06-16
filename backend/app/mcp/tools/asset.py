"""平台资产创建工具 `hasn.asset.create`（分身把自己的内容上传成 `hasn://asset` 资产）。

补齐分身「发图 / 发附件」链路缺的中间一环：分身生成或持有的内容（自己画的 SVG、
base64 图片字节、文本 / 文档……）经本工具落 owner 私有桶 + 注册 `hasn_assets`，返回
`hasn://asset/{id}`，随后用 `hasn.message.send(attachments=[...])` 发出去。
原本分身只能引用 `hasn.image.generate` / `hasn.voice.synthesize` 的**生成**产物，无法把
**自己手上的内容**变成可发送的资产——这是缺口。

与既有上传面的关系：
- owner WebUI 走 `POST /api/v1/app/hasn_assets/upload`（Owner JWT，multipart）。
- 分身**没有** multipart 通道（经 MCP tool-call 调用），故本工具收 base64 / 原文文本
  字符串，等价地落桶 + 注册。资产归属 `owner_hasn_id`（取自 Agent 凭证），与
  `hasn.message.send` 的「附件必须属本主人」校验天然对齐。

零 Fake + 安全：
- 资产 `owner_hasn_id` 取自 Agent JWT（`AgentContext.owner_hasn_id`），不由分身自报。
- 大小按 kind 限额（图 10MB / 语音 25MB / 文件 50MB），与 owner 端点一致；超限 / 空 /
  非法 base64 一律抛错暴露，不伪造。
- 私有桶（`category=dm_attachment`），与消息附件同语义；展示经 resolve 鉴权签名。

产物自动登记（docs/Agent产物系统/00-...md §4.3 / D2「文件上传类」）：
- 上传成功后在同事务内 best-effort 登记一条 `hasn_artifacts`（`source_kind='upload'`），分身上传的
  内容即进 owner 的产物时间线，无需分身显式调记录工具。用 SAVEPOINT 隔离——登记失败只回滚该行、
  不连累已落桶的资产，仅告警（§8 best-effort：捕获失败不阻断工具结果）。
"""

from __future__ import annotations

import base64
import binascii
import logging

from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import storage_service

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

logger = logging.getLogger(__name__)

# 与 owner 上传端点 _MAX_SIZE 对齐（hasn_assets_app.py）：图 10MB / 语音 25MB / 文件 50MB。
_MAX_SIZE = {'image': 10 * 1024 * 1024, 'voice': 25 * 1024 * 1024, 'file': 50 * 1024 * 1024}

# 资产无原始文件名时，按 mime 合成下载名后缀。
_MIME_EXT = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/svg+xml': '.svg',
    'audio/mpeg': '.mp3',
    'audio/mp4': '.m4a',
    'audio/wav': '.wav',
    'audio/ogg': '.ogg',
    'application/pdf': '.pdf',
    'text/plain': '.txt',
    'text/markdown': '.md',
}

_ENCODING_BASE64 = 'base64'
_ENCODING_TEXT = 'text'
_TEXT_ENCODING_ALIASES = frozenset({_ENCODING_TEXT, 'utf8', 'utf-8', 'raw'})


def kind_of_mime(mime: str) -> str:
    """MIME → 资产 kind（与 owner 端点 `_kind_of` 同口径）。"""
    normalized = (mime or '').strip().lower()
    if normalized.startswith('image/'):
        return 'image'
    if normalized.startswith('audio/'):
        return 'voice'
    return 'file'


def decode_content(content: str, encoding: str) -> bytes:
    """把分身传入的 content 解成字节。

    - `encoding='base64'`（默认）：二进制字节的 base64（PNG/JPEG/语音/PDF……）。
    - `encoding='text'`：UTF-8 原文（分身自己写的 SVG/markdown/纯文本，免 base64）。

    零 fake：非法 base64 直接抛错，不静默丢弃。
    """
    enc = (encoding or _ENCODING_BASE64).strip().lower()
    if enc in _TEXT_ENCODING_ALIASES:
        return content.encode('utf-8')
    if enc == _ENCODING_BASE64:
        try:
            # validate=True：遇非 base64 字符报错，而非静默忽略（避免落入坏字节）。
            return base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(f'content 不是合法 base64：{exc}') from exc
    raise RuntimeError(f"不支持的 encoding：{encoding}（应为 '{_ENCODING_BASE64}' 或 '{_ENCODING_TEXT}'）")


def derive_filename(filename: str | None, kind: str, mime: str) -> str:
    """显式 filename 优先；缺省按 kind + mime 合成（image.png / voice.mp3 / file.pdf）。"""
    if filename and filename.strip():
        return filename.strip()
    base = {'image': 'image', 'voice': 'voice', 'file': 'file'}.get(kind, 'file')
    return f'{base}{_MIME_EXT.get((mime or "").strip().lower(), "")}'


def _opt_positive_int(value: Any) -> int | None:
    """可选正整数解析：非数 / 非正 → None（宽容，宽高/时长只作展示提示）。"""
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class AssetCreateTool(BaseTool):
    """把分身的内容上传成资产，返回 `hasn://asset/{id}`（供 `hasn.message.send` 等附件引用）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.asset.create'

    @property
    def namespace(self) -> str:
        return 'hasn.asset'

    @property
    def description(self) -> str:
        return (
            '把你自己的内容上传为资产并返回 hasn://asset/{id}（随后用 hasn.message.send 的 '
            'attachments 发出去）。文本类内容（你写的 SVG、markdown、纯文本）直接传原文并设 '
            "encoding='text'；二进制类（PNG/JPEG/语音/PDF）传 base64（默认 encoding='base64'）。"
            '资产归你的主人所有、私有存储。'
        )

    @property
    def risk_level(self) -> str:
        return 'medium'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'content': {
                    'type': 'string',
                    'description': (
                        '要上传的内容。文本类（SVG/markdown/纯文本）直接传原文（配 encoding="text"）；'
                        '二进制类（图片/语音/文件）传字节的 base64（配 encoding="base64"，默认）。'
                    ),
                },
                'mime': {
                    'type': 'string',
                    'description': (
                        '内容的 MIME 类型，决定资产 kind 与渲染/下载方式。'
                        '例：image/png、image/svg+xml、image/jpeg、audio/mpeg、application/pdf、text/plain。'
                    ),
                },
                'encoding': {
                    'type': 'string',
                    'enum': [_ENCODING_BASE64, _ENCODING_TEXT],
                    'description': 'content 编码：base64=二进制字节的 base64（默认）；text=UTF-8 原文（SVG/文本用）。',
                },
                'filename': {
                    'type': 'string',
                    'description': '可选：下载文件名（含扩展名）；缺省按 kind+mime 合成。',
                },
                'width': {'type': 'integer', 'description': '可选：图片宽（px），便于前端布局。'},
                'height': {'type': 'integer', 'description': '可选：图片高（px），便于前端布局。'},
                'duration_ms': {'type': 'integer', 'description': '可选：音频时长（毫秒）。'},
            },
            'required': ['content', 'mime'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['asset:create']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """解码内容 → storage_service 落私有桶 → register_asset → 返回 hasn://asset/{id}。

        维度① 能力授权已在 server.call_tool 按三态 mode 统一判定（D3），工具内不二次校验。
        """
        owner_hasn_id = agent_context.owner_hasn_id
        if not owner_hasn_id:
            raise RuntimeError('无法解析主人身份（owner_hasn_id 缺失），无法归属资产')

        content = arguments.get('content')
        if not content:
            raise RuntimeError('缺少必填参数：content')
        mime = str(arguments.get('mime') or '').strip()
        if not mime:
            raise RuntimeError('缺少必填参数：mime')

        data = decode_content(str(content), str(arguments.get('encoding') or _ENCODING_BASE64))
        if not data:
            raise RuntimeError('内容为空')

        kind = kind_of_mime(mime)
        limit = _MAX_SIZE.get(kind, _MAX_SIZE['file'])
        if len(data) > limit:
            raise RuntimeError(f'{kind} 超出大小上限 {limit // (1024 * 1024)}MB')

        width = _opt_positive_int(arguments.get('width'))
        height = _opt_positive_int(arguments.get('height'))
        duration_ms = _opt_positive_int(arguments.get('duration_ms'))
        filename = derive_filename(arguments.get('filename'), kind, mime)

        async with async_db_session() as db:
            ref = await storage_service.upload(db, data, category='dm_attachment', filename=filename, content_type=mime)
            asset = await hasn_asset_service.register_asset(
                db,
                owner_hasn_id=owner_hasn_id,
                ref=ref,
                kind=kind,
                width=width,
                height=height,
                duration_ms=duration_ms,
            )
            # 产物自动登记（设计 §4.3 / D2「文件上传类」→ source_kind='upload'）：
            # 分身把自己的内容上传即一条产物，进 owner 产物时间线，无需分身显式调记录工具。
            # best-effort：用 SAVEPOINT 隔离，登记失败只回滚这一条、不连累已落桶的资产，仅告警不抛错（§8）。
            await self._record_artifact_best_effort(
                db, agent_context=agent_context, owner_hasn_id=owner_hasn_id, asset=asset, kind=kind,
                mime=ref.mime, size=ref.size, filename=filename, width=width, height=height, duration_ms=duration_ms,
            )
            # async_db_session() 退出只 close 不 commit；写资产须显式提交，否则回滚不落库。
            await db.commit()

        return {
            'asset_id': asset.asset_id,
            'uri': f'hasn://asset/{asset.asset_id}',
            'kind': kind,
            'mime': ref.mime,
            'size': ref.size,
            'width': width,
            'height': height,
            'duration_ms': duration_ms,
        }

    async def _record_artifact_best_effort(
        self,
        db: Any,
        *,
        agent_context: AgentContext,
        owner_hasn_id: str,
        asset: Any,
        kind: str,
        mime: str,
        size: int,
        filename: str,
        width: int | None,
        height: int | None,
        duration_ms: int | None,
    ) -> None:
        """把刚上传的资产登记成一条分身产物（best-effort）。

        身份取自 Agent 凭证（不自报）：分身 = `agent_context.agent_hasn_id`、归属 = `owner_hasn_id`。
        上传场景无会话/消息上下文（资产先生成、后单独 `hasn.message.send` 引用），故不带 conversation/message，
        产物跳转锚诚实留空。用 SAVEPOINT 隔离：登记失败只回滚这一条、不连累已落桶的资产，仅告警不抛错。
        """
        metadata: dict[str, Any] = {'mime': mime, 'size': size}
        if width is not None:
            metadata['width'] = width
        if height is not None:
            metadata['height'] = height
        if duration_ms is not None:
            metadata['duration_ms'] = duration_ms
        try:
            async with db.begin_nested():  # SAVEPOINT：登记失败只回滚这一行，资产仍提交。
                await hasn_artifacts_service.record(
                    db,
                    agent_hasn_id=agent_context.agent_hasn_id,
                    owner_hasn_id=owner_hasn_id,
                    params=RecordArtifactParam(
                        kind=kind,
                        title=filename,
                        asset_id=asset.asset_id,
                        source_tool=self.name,
                        source_kind='upload',
                        metadata=metadata,
                    ),
                )
        except Exception as exc:  # best-effort：任何登记异常都吞掉只告警，绝不阻断已成功的资产上传
            logger.warning(
                'hasn.asset.create 产物自动登记失败（资产 %s 已上传，不阻断）：%s',
                asset.asset_id,
                exc,
                exc_info=True,
            )
