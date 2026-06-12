"""
平台消息工具（14-doc §2 能力 4/5）

hasn.message.send 走 message_router 真实路由（目标解析→关系/权限判决→会话→持久化→投递→
主人透明），替代旧 `hasn_messages_service.create` 裸插（G1）。维度②对象可达性由路由内
permission_engine 判定，工具返回 reachable/reason（不可达不静默成功），与维度①正交。

富媒体（MEDIA-P3）：除文本外，分身可发图片/语音/文件（attachments，资产 URI）与信息卡片
（card）。content 结构对齐人类发送路径（ws_node `content_type` 字符串→整数 + body 抽取）：
content_type 1=文本/2=图片/3=文件/4=语音/5=卡片；富媒体 body={text, attachments:[{uri,kind,
mime,name,size,...}]}，卡片 body=CardMessageBody。零 Fake：附件元数据来自 hasn_assets 注册表，
拿不到/非本主人资产一律拒绝不伪造。
"""

from typing import Any

from sqlalchemy import select

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.service import message_router
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session


# content_type 整数码：对齐 message_router.persist_message 预览分支 + ws_node 字符串映射。
_CT_TEXT = 1
_CT_IMAGE = 2
_CT_FILE = 3
_CT_VOICE = 4
_CT_CARD = 5

_KIND_TO_CONTENT_TYPE = {'image': _CT_IMAGE, 'file': _CT_FILE, 'voice': _CT_VOICE}
_MIME_EXT = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'audio/mpeg': '.mp3',
    'audio/mp4': '.m4a',
    'audio/wav': '.wav',
    'audio/ogg': '.ogg',
    'application/pdf': '.pdf',
    'text/plain': '.txt',
}


def _asset_id_from_uri(uri: Any) -> str | None:
    """hasn://asset/{asset_id} → asset_id。"""
    if isinstance(uri, str) and uri.startswith('hasn://asset/'):
        candidate = uri[len('hasn://asset/'):].strip('/')
        return candidate or None
    return None


def _derive_name(kind: str, mime: str) -> str:
    """资产无原始文件名 → 按 kind + mime 合成一个可下载名（image.jpg / voice.mp3 / file.pdf）。"""
    base = {'image': 'image', 'voice': 'voice', 'file': 'file'}.get(kind, 'file')
    return f'{base}{_MIME_EXT.get(mime, "")}'


async def _resolve_attachments(
    db: Any, owner_hasn_id: str | None, uris: list[Any]
) -> tuple[list[dict[str, Any]], int]:
    """资产 URI 列表 → 真实附件元数据 + 推断 content_type（按首个附件 kind）。

    零 Fake + 安全：
    - 元数据全部取自 hasn_assets 注册表（kind/mime/size/宽高/时长），不由分身自报。
    - 资产必须属本主人（owner_hasn_id），否则拒绝——防分身附带他人资产致跨会话越权 grant。
    """
    asset_ids: list[str] = []
    for u in uris:
        aid = _asset_id_from_uri(u)
        if not aid:
            raise RuntimeError(f'非法资产引用（应为 hasn://asset/{{id}}）: {u}')
        asset_ids.append(aid)

    assets = await hasn_asset_service.get_many(db, asset_ids)
    attachments: list[dict[str, Any]] = []
    first_kind: str | None = None
    for aid in asset_ids:
        asset = assets.get(aid)
        if asset is None:
            raise RuntimeError(f'资产不存在: {aid}')
        if owner_hasn_id and asset.owner_hasn_id != owner_hasn_id:
            raise RuntimeError(f'资产不属于你的主人，拒绝发送: {aid}')
        attachment: dict[str, Any] = {
            'uri': f'hasn://asset/{aid}',
            'kind': asset.kind,
            'mime': asset.mime,
            'name': _derive_name(asset.kind, asset.mime),
            'size': asset.size_bytes,
        }
        if asset.width is not None:
            attachment['width'] = asset.width
        if asset.height is not None:
            attachment['height'] = asset.height
        if asset.duration_ms is not None:
            attachment['duration_ms'] = asset.duration_ms
        attachments.append(attachment)
        if first_kind is None:
            first_kind = asset.kind

    content_type = _KIND_TO_CONTENT_TYPE.get(first_kind or 'file', _CT_FILE)
    return attachments, content_type


def _build_agent_card_body(
    agent_hasn_id: str, agent_display_name: str, card_input: dict[str, Any]
) -> dict[str, Any]:
    """分身的简化卡片输入 → 完整 CardMessageBody（信息卡）。

    安全：分身只提供 title/description/fields/link，构造时**只生成 open_uri 主操作**，
    绝不接受 grant/deny_authorization、invoke_tool、authorization_request——从源头杜绝
    分身向任意联系人发送可执行/授权类卡片。validate_card_message_body 再做一道 schema 校验。
    """
    if not isinstance(card_input, dict):
        raise RuntimeError('card 必须是对象')
    title = str(card_input.get('title') or '').strip()
    if not title:
        raise RuntimeError('card.title 必填且非空')

    fields: list[dict[str, str]] = []
    for field in card_input.get('fields') or []:
        if isinstance(field, dict):
            fields.append({'label': str(field.get('label', '')), 'value': str(field.get('value', ''))})

    primary_action = None
    link = card_input.get('link')
    if isinstance(link, dict) and link.get('uri'):
        primary_action = {
            'action_id': 'open',
            'label': str(link.get('label') or '打开'),
            'kind': 'open_uri',
            'uri': str(link['uri']),
            'style': 'primary',
        }

    body = {
        'schema_version': 'hasn.card/0.1',
        'title': title,
        'description': str(card_input['description']) if card_input.get('description') else None,
        'source': {
            'kind': 'agent',
            'id': agent_hasn_id,
            'display_name': agent_display_name or agent_hasn_id,
            'verified': False,
        },
        'resource': {
            'type': 'custom',
            'id': f'agent-card-{agent_hasn_id}',
            'uri': f'hasn://agent/{agent_hasn_id}',
        },
        'fields': fields,
        'primary_action': primary_action,
        'actions': [],
        'metadata': {},
    }
    # 校验失败抛 errors.RequestError（含 uri scheme / 非法字段）。
    return validate_card_message_body(body).model_dump()


async def _resolve_agent_display_name(db: Any, agent_hasn_id: str, fallback: str) -> str:
    result = await db.execute(select(HasnAgents.display_name).where(HasnAgents.hasn_id == agent_hasn_id))
    return result.scalar() or fallback


class MessageSendTool(BaseTool):
    """发送私信工具——文本 + 富媒体（图片/语音/文件/卡片），走真实路由 + 关系门控 + 主人透明（G1/MEDIA-P3）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.message.send'

    @property
    def namespace(self) -> str:
        return 'hasn.message'

    @property
    def description(self) -> str:
        return (
            '给某用户/Agent/会话发消息（走真实路由：解析目标→关系权限→会话→投递→主人透明）。'
            '可发：纯文本（content）、图片/语音/文件（attachments 传 hasn://asset/ 资产引用，'
            '来自 hasn.image.generate / hasn.voice.synthesize 等）、信息卡片（card：title 必填，'
            '可含 description/fields/link）。'
        )

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'to': {'type': 'string', 'description': '接收者 HASN ID / 唤星号 / 会话目标'},
                'content': {
                    'type': 'string',
                    'description': '消息文本内容（纯文本消息必填；发附件/卡片时作为附带说明，可选）',
                },
                'attachments': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': (
                        '要发送的资产引用列表（hasn://asset/{id}，来自 hasn.image.generate / '
                        'hasn.voice.synthesize 等）。发图片/语音/文件时用；元数据由平台按资产解析。'
                    ),
                },
                'card': {
                    'type': 'object',
                    'description': '要发送的信息卡片（title 必填，可含 description / fields / link）。',
                    'properties': {
                        'title': {'type': 'string'},
                        'description': {'type': 'string'},
                        'fields': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'label': {'type': 'string'},
                                    'value': {'type': 'string'},
                                },
                            },
                            'description': '卡片字段列表（label/value 键值对）',
                        },
                        'link': {
                            'type': 'object',
                            'properties': {
                                'label': {'type': 'string'},
                                'uri': {'type': 'string'},
                            },
                            'description': '卡片底部「打开」按钮（uri 支持 hasn:// / http(s)://）',
                        },
                    },
                },
            },
            'required': ['to'],
        }

    @property
    def required_scopes(self) -> list[str]:
        # G7：message:write → message:send（语义更准，14-doc §3）
        return ['message:send']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """走 message_router.route_message 真实投递；文本/图片/语音/文件/卡片统一出口。

        维度① 能力授权已在 server.call_tool 按三态 mode 统一判定（D3），工具内不二次校验。
        """
        to_target = arguments.get('to')
        if not to_target:
            raise RuntimeError('Missing required arguments: to')

        text = arguments.get('content')
        attachments_uris = arguments.get('attachments') or []
        card_input = arguments.get('card')

        if not card_input and not attachments_uris and not (text and str(text).strip()):
            raise RuntimeError('需提供 content（文本）或 attachments（附件）或 card（卡片）之一')

        async with async_db_session() as db:
            if card_input:
                display_name = await _resolve_agent_display_name(
                    db, agent_context.agent_hasn_id, agent_context.agent_name or ''
                )
                content = _build_agent_card_body(agent_context.agent_hasn_id, display_name, card_input)
                content_type = _CT_CARD
            elif attachments_uris:
                attachments, content_type = await _resolve_attachments(
                    db, agent_context.owner_hasn_id, attachments_uris
                )
                content = {'text': str(text) if text else '', 'attachments': attachments}
            else:
                content = {'text': str(text)}
                content_type = _CT_TEXT

            # 身份取自 Agent 凭证（agent_hasn_id），不用 owner_user_id 冒名（G2）。
            result = await message_router.route_message(
                db=db,
                from_id=agent_context.agent_hasn_id,
                to_target=str(to_target),
                content=content,
                content_type=content_type,
                msg_type='message',
            )

        # 维度②：路由内 permission_engine（关系/信任）判决，不可达不静默成功。
        if result.get('error'):
            return {
                'message_id': None,
                'conversation_id': None,
                'delivered': False,
                'reachable': False,
                'reason': result.get('message', ''),
                'code': result.get('code'),
            }

        status = result.get('status')
        # CONFIRM 决策（如需主人确认）→ 已挂起，未送达但目标可达
        if status == 'pending_confirmation':
            return {
                'message_id': None,
                'conversation_id': result.get('conversation_id'),
                'delivered': False,
                'reachable': True,
                'status': status,
                'reason': result.get('reason', ''),
            }
        return {
            'message_id': result.get('msg_id'),
            'conversation_id': result.get('conversation_id'),
            'delivered': status == 'sent',
            'reachable': True,
            'status': status,
        }


def _extract_text(envelope: dict[str, Any]) -> str:
    """从 HASN envelope 里尽力取出可读文本（content 可能是 dict 或 str）。"""
    content = envelope.get('content')
    if isinstance(content, dict):
        return str(content.get('text') or content.get('content') or '')
    if isinstance(content, str):
        return content
    return ''


def _extract_sender(envelope: dict[str, Any]) -> str:
    """从 envelope 取出发送方 hasn_id（兼容多种字段命名）。"""
    routing = envelope.get('routing') if isinstance(envelope.get('routing'), dict) else {}
    return str(
        envelope.get('from')
        or envelope.get('from_hasn_id')
        or envelope.get('sender_hasn_id')
        or routing.get('from')
        or ''
    )


class MessageListTool(BaseTool):
    """获取主人收件箱消息列表（含主人透明副本）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.message.list'

    @property
    def namespace(self) -> str:
        return 'hasn.message'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return '获取收件箱消息列表（最近的会话消息，含发送方/文本/会话/时间）'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'description': '返回数量限制（默认 20）', 'minimum': 1, 'maximum': 100}
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['message:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        from backend.app.hasn.schema.hasn_message_hub import InboxPullRequest
        from backend.app.hasn.service.hasn_message_hub_service import HasnMessageHubService

        limit = min(max(int(arguments.get('limit', 20)), 1), 100)
        async with async_db_session() as db:
            message_service = HasnMessageHubService()
            # 收件箱按 owner 维度拉取（主人透明：含 Agent 会话的 owner_copy）。
            pull_request = InboxPullRequest(owner_id=agent_context.owner_hasn_id)
            result = await message_service.pull_inbox(db, pull_request)

            messages = []
            for item in result.items[:limit]:
                envelope = item.envelope or {}
                messages.append({
                    'message_id': str(item.message_id),
                    'conversation_id': str(item.conversation_id),
                    'inbox_kind': getattr(item.inbox_kind, 'value', item.inbox_kind),
                    'from': _extract_sender(envelope),
                    'content': _extract_text(envelope),
                    'created_at': str(item.created_at),
                })
            return {'messages': messages, 'has_more': result.has_more, 'next_cursor': result.next_cursor}
