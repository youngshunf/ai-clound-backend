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

from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn.service import message_router
from backend.app.hasn.service.agent_message_read_service import agent_message_read_service
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_core import HasnAgents
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

# content_type 整数码：对齐 message_router.persist_message 预览分支 + ws_node 字符串映射。
_CT_TEXT = 1
_CT_IMAGE = 2
_CT_FILE = 3
_CT_VOICE = 4
_CT_CARD = 5

# 寻址主人的保留哨兵：分身的每轮身份注入只给主人画像自由文本，**不含**主人 hasn_id/唤星号
# （identity-owner 模板只注入 owner_portrait.text），分身无法可靠拿到主人的 `to`。云端 AgentContext
# 已带 owner_hasn_id，故在工具层把这两个保留值解析成主人本人——让「通知主人」对任何技能都可靠
# （deck 产出卡片、获客/简报回流等共用）。`@` 前缀不会与 hasn_id（h_/a_）或唤星号（数字/自定义 id）撞。
_OWNER_TARGET_SENTINELS = {'owner', '@owner'}

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


def _resolve_to_target(to_target: Any, owner_hasn_id: str | None) -> str:
    """把分身给的 `to` 解析成真实接收者：保留值 "owner"/"@owner" → 主人本人，其余原样透传。

    分身的身份注入只含主人画像自由文本、不含主人 hasn_id/唤星号，故给保留哨兵让「通知主人」可靠。
    主人身份缺失（owner_hasn_id 为空）→ 抛错不静默，避免把卡片误发到错误目标。
    """
    if str(to_target).strip().lower() in _OWNER_TARGET_SENTINELS:
        if not owner_hasn_id:
            raise RuntimeError('无法解析主人身份（owner_hasn_id 缺失），请稍后重试或指定具体接收者')
        return owner_hasn_id
    return str(to_target)


def _asset_id_from_uri(uri: Any) -> str | None:
    """hasn://asset/{asset_id} → asset_id。"""
    if isinstance(uri, str) and uri.startswith('hasn://asset/'):
        candidate = uri[len('hasn://asset/') :].strip('/')
        return candidate or None
    return None


def _derive_name(kind: str, mime: str) -> str:
    """资产无原始文件名 → 按 kind + mime 合成一个可下载名（image.jpg / voice.mp3 / file.pdf）。"""
    base = {'image': 'image', 'voice': 'voice', 'file': 'file'}.get(kind, 'file')
    return f'{base}{_MIME_EXT.get(mime, "")}'


async def _resolve_attachments(db: Any, owner_hasn_id: str | None, uris: list[Any]) -> tuple[list[dict[str, Any]], int]:
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


def _build_agent_card_body(agent_hasn_id: str, agent_display_name: str, card_input: dict[str, Any]) -> dict[str, Any]:
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

    fields: list[dict[str, str]] = [
        {'label': str(field.get('label', '')), 'value': str(field.get('value', ''))}
        for field in card_input.get('fields') or []
        if isinstance(field, dict)
    ]

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


async def _ensure_first_contact_request(owner_hasn_id: str, to_target: Any) -> int | None:
    """消息被暂存且无关系时，代主人自动发一条好友请求（§4.1.4，幂等复用）。

    复用 request_contact 单一实现（已有 pending 幂等返回其 id，不重复建）；业务校验失败/已是好友
    等异常一律吞掉返回 None（自动代发是「顺手帮忙」，绝不因此把 message.send 打成错误）。
    """
    from backend.app.hasn.service.hasn_contacts_service import (
        ContactRequestError,
        HasnContactsService,
    )

    async with async_db_session() as db:
        try:
            res = await HasnContactsService.request_contact(
                db,
                requester_hasn_id=owner_hasn_id,
                target=str(to_target),
                message=None,
                add_source='agent_message_autorequest',
            )
            return res.get('request_id')
        except ContactRequestError:
            return None
        except Exception:  # noqa: BLE001 - 自动代发失败不影响主流程
            return None


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
            '来自 hasn.image.generate / hasn.voice.synthesize 生成，或本地 hasn.asset.upload(path) 上传）、'
            '信息卡片（card：title 必填，可含 description/fields/link）。'
            '若返回 status="pending_contact_approval"，表示尚未与对方建立联系人关系、消息已暂存并已'
            '**自动代发好友请求**（见返回 pending_request_id）；此时 reachable=false，不要改用其它方式'
            '重试或重复发送，等对方（或其主人）通过后再发即可。主动加好友用 hasn.contact.request。'
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
                'to': {
                    'type': 'string',
                    'description': (
                        '接收者 HASN ID / 唤星号 / 会话目标；发给你自己的主人时填保留值 "owner"'
                        '（你的身份注入不含主人 id，要通知主人就用 "owner"，别去猜主人的 HASN ID）。'
                    ),
                },
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

        # 主人寻址哨兵 → 解析为主人本人（身份注入不含主人 id，见 _OWNER_TARGET_SENTINELS 注释）。
        to_target = _resolve_to_target(to_target, agent_context.owner_hasn_id)

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

        # 被暂存拦截箱（未建立联系人关系/低信任）→ 结构化关系反馈（修 B12：不再误报 reachable=true）。
        # 无关系时自动代发好友请求（幂等复用入站门控已代发的那条，不重复建）。
        if status == 'suppressed' or result.get('suppressed'):
            pending_request_id = result.get('pending_request_id')
            if not pending_request_id:
                pending_request_id = await _ensure_first_contact_request(
                    agent_context.owner_hasn_id, to_target,
                )
            return {
                'message_id': result.get('msg_id'),
                'conversation_id': result.get('conversation_id'),
                'delivered': False,
                'reachable': False,
                'status': 'pending_contact_approval',
                'relation': result.get('relation'),
                'pending_request_id': pending_request_id,
                'hint': (
                    '尚未与对方建立联系人关系，已自动代发好友请求；须对方（或其主人）同意后消息才能'
                    '送达。请勿改用其它方式重试，也不要重复发送——耐心等待对方通过即可。'
                ),
            }

        return {
            'message_id': result.get('msg_id'),
            'conversation_id': result.get('conversation_id'),
            'delivered': status == 'sent',
            'reachable': True,
            'status': status,
        }


class MessageListTool(BaseTool):
    """获取主人聊天记录（主人透明视图），默认倒序 + keyset 翻页 + 可按会话过滤。

    走 agent_message_read_service（owner-scoped 只读查询），修掉旧版「最旧优先 + cursor 假翻页」的坑：
    - 默认**最新在前**（按消息 id 倒序）；
    - `cursor` 传上一页返回的 `next_cursor` 向更早翻页，稳定不漂移；
    - 传 `conversation_id` 即只看该会话的消息（会话详情）。
    """

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
        return (
            '读取主人的聊天记录（主人透明视图），**默认按时间倒序=最新在前**，含发送方/内容/会话/时间。'
            '传 conversation_id 只看某个会话的消息（会话详情）；用 cursor+limit 向更早翻页'
            '（cursor 传上一次返回的 next_cursor）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'conversation_id': {
                    'type': 'string',
                    'description': '只看该会话的消息（会话 ID，来自 hasn.conversation.list）；不传=看全部收件箱。',
                },
                'limit': {
                    'type': 'integer',
                    'description': '返回数量（默认 20，最大 100）',
                    'minimum': 1,
                    'maximum': 100,
                },
                'cursor': {
                    'type': 'string',
                    'description': '翻页游标：传上一次返回的 next_cursor 拉更早的一页；不传=从最新开始。',
                },
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['message:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        async with async_db_session() as db:
            return await agent_message_read_service.list_messages(
                db,
                agent_context.owner_hasn_id,
                limit=arguments.get('limit', 20),
                cursor=arguments.get('cursor'),
                conversation_id=arguments.get('conversation_id'),
            )


class ConversationListTool(BaseTool):
    """列出主人的会话（每会话一行 + 最后消息预览），按最近活动倒序。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.conversation.list'

    @property
    def namespace(self) -> str:
        return 'hasn.conversation'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '列出主人的会话列表（每个会话一行，含类型/标题/参与方/最后一条消息预览/发送方/时间），'
            '按最近活动倒序。配合 hasn.message.list(conversation_id=...) 下钻某个会话看详细消息。'
            '用 cursor+limit 向更早翻页（cursor 传上一次返回的 next_cursor）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'limit': {
                    'type': 'integer',
                    'description': '返回数量（默认 20，最大 100）',
                    'minimum': 1,
                    'maximum': 100,
                },
                'cursor': {
                    'type': 'string',
                    'description': '翻页游标：传上一次返回的 next_cursor 拉更早的一页；不传=从最近开始。',
                },
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['message:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        async with async_db_session() as db:
            return await agent_message_read_service.list_conversations(
                db,
                agent_context.owner_hasn_id,
                limit=arguments.get('limit', 20),
                cursor=arguments.get('cursor'),
            )


class MessageSearchTool(BaseTool):
    """按关键词搜索主人的聊天记录（文本 + 卡片标题/描述），倒序返回。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.message.search'

    @property
    def namespace(self) -> str:
        return 'hasn.message'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '按关键词搜索主人的聊天记录（在消息文本与卡片标题/描述上大小写不敏感匹配），**按时间倒序**返回。'
            '传 conversation_id 可限定只在某个会话内搜；用 cursor+limit 翻页（cursor 传上一次返回的 next_cursor）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '搜索关键词（必填，非空）'},
                'conversation_id': {
                    'type': 'string',
                    'description': '只在该会话内搜（会话 ID）；不传=全部聊天记录里搜。',
                },
                'limit': {
                    'type': 'integer',
                    'description': '返回数量（默认 20，最大 100）',
                    'minimum': 1,
                    'maximum': 100,
                },
                'cursor': {
                    'type': 'string',
                    'description': '翻页游标：传上一次返回的 next_cursor 拉更早的一页。',
                },
            },
            'required': ['query'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['message:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get('query')
        if not query or not str(query).strip():
            raise RuntimeError('Missing required arguments: query')
        async with async_db_session() as db:
            return await agent_message_read_service.search_messages(
                db,
                agent_context.owner_hasn_id,
                str(query),
                limit=arguments.get('limit', 20),
                cursor=arguments.get('cursor'),
                conversation_id=arguments.get('conversation_id'),
            )
