"""Agent 消息读能力（MSGTOOL）——供云端平台工具 message.list / conversation.list / message.search 使用。

设计要点：
- **owner 隔离**：一律 `WHERE owner_id = :owner`（主人透明视图，含分身会话的 owner_copy），
  分身只能读自己主人的消息，绝不跨 owner。
- **默认倒序 + keyset 翻页**：按 `hasn_messages.id`（BIGINT 自增，与时序同向）`id < cursor` 倒序翻页，
  最新在前；游标就是上一页最后一条的 id，稳定不漂移。修掉旧 message.list「最旧优先 + cursor 假翻页」的坑。
- **不复用 owner 同步读取面**：Agent JWT 只能读取自身消息，不能继承 owner 的可见范围。
- **零 Fake**：读不到就返回空列表 + has_more=False，绝不编造。

content 为 jsonb：文本消息 body={text,...}，卡片 body={title,description,...}。预览/搜索在这几个键上取值，
不做全 jsonb 结构匹配（避免命中 "attachments" 等结构键造成误报）。
"""

from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

_MAX_LIMIT = 100
_DEFAULT_LIMIT = 20


def _parse_cursor(cursor: str | None) -> int | None:
    """游标 → bigint id。兼容裸 id 与历史 's4:{id}' 前缀；非法/空 → None（从头开始）。"""
    if cursor is None:
        return None
    raw = str(cursor).strip()
    if not raw:
        return None
    tail = raw.rsplit(':', maxsplit=1)[-1]
    try:
        return int(tail)
    except (TypeError, ValueError):
        return None


def _clamp_limit(limit: Any) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return min(max(value, 1), _MAX_LIMIT)


# conversation.list 的 `last_message` 预览上限（doc13 决策 G 防御纵深）——列表预览本就不该是全文，
# 末条若是分身自发长回复可达几千 token，源头就设个宽上限，别把整条灌回 daemon。
# ⚠️ 只用于 conversation.list 预览；message.list 的 content 是分身下钻要读的正文，**不截**。
_CONVERSATION_PREVIEW_CAP = 120


def _preview(content: Any, *, cap: int | None = None) -> str:
    """从 content（dict/str/None）尽力取可读文本：文本 body.text；卡片 title[ - description]。

    `cap` 非空时把结果硬截断到该长度（+省略号）——仅列表预览（conversation.list）传，
    message.list 正文下钻不传（要全文）。
    """
    if isinstance(content, dict):
        text = content.get('text') or content.get('content')
        if text:
            result = str(text)
        else:
            title = content.get('title')
            if title:
                desc = content.get('description')
                result = f'{title} - {desc}' if desc else str(title)
            else:
                result = ''
    elif isinstance(content, str):
        result = content
    else:
        result = ''
    if cap is not None and len(result) > cap:
        return result[:cap] + '…'
    return result


def _sender_of(row: Any) -> str:
    """best-effort 发送方 hasn_id：sender_hasn_id > from_id > hasn_id（owner_copy 行可能只填其一）。"""
    for key in ('sender_hasn_id', 'from_id', 'hasn_id'):
        value = row.get(key)
        if value:
            return str(value)
    return ''


def _attachments_of(content: Any) -> list[Any]:
    """从 content(jsonb) best-effort 取附件列表——兼容顶层 `attachments` 与 `body.attachments` 两种内联形状；无则空列表。

    群历史工具要把图片/语音/文件等附件随行返回给分身（别丢 attachments），而附件在 content jsonb 里内联，
    没有独立 attachments 列，故这里就地取值。
    """
    if not isinstance(content, dict):
        return []
    direct = content.get('attachments')
    if isinstance(direct, list):
        return direct
    body = content.get('body')
    if isinstance(body, dict) and isinstance(body.get('attachments'), list):
        return body['attachments']
    return []


class AgentMessageReadService:
    """云端平台工具专用的只读消息/会话查询（owner-scoped, 倒序, keyset 翻页）。"""

    async def list_messages(
        self,
        db: AsyncSession,
        owner_id: str,
        *,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """收件箱 / 会话详情（给 conversation_id 即按会话过滤），默认倒序 + keyset 翻页。"""
        lim = _clamp_limit(limit)
        cursor_id = _parse_cursor(cursor)
        conv = (conversation_id or '').strip() or None

        rows = (
            (
                await db.execute(
                    sa.text(
                        """
                    SELECT id,
                           conversation_id::text AS conversation_id,
                           from_id,
                           hasn_id,
                           sender_hasn_id,
                           content_type,
                           content,
                           created_time
                    FROM public.hasn_messages
                    WHERE owner_id = :owner_id
                      AND (CAST(:conv AS text) IS NULL OR conversation_id = CAST(:conv AS uuid))
                      AND (CAST(:cursor_id AS bigint) IS NULL OR id < CAST(:cursor_id AS bigint))
                    ORDER BY id DESC
                    LIMIT :limit
                    """
                    ),
                    {'owner_id': owner_id, 'conv': conv, 'cursor_id': cursor_id, 'limit': lim + 1},
                )
            )
            .mappings()
            .all()
        )

        has_more = len(rows) > lim
        page = rows[:lim]
        messages = [
            {
                'message_id': str(r['id']),
                'conversation_id': r['conversation_id'],
                'from': _sender_of(r),
                'content_type': r['content_type'],
                'content': _preview(r['content']),
                'created_at': str(r['created_time']),
            }
            for r in page
        ]
        next_cursor = str(page[-1]['id']) if page and has_more else None
        return {'messages': messages, 'has_more': has_more, 'next_cursor': next_cursor}

    async def list_group_messages(
        self,
        db: AsyncSession,
        conversation_id: str,
        *,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """群聊历史：按 conversation_id 拉全群消息，默认倒序 + keyset 翻页。

        与 list_messages 几乎同构，**唯一差别是不按 owner_id 过滤、改按 conversation_id**：群消息在云端
        hasn_messages 里只单条存储、不为成员落 owner_copy 副本行，故 owner 作用域查询看不到群历史；这里按
        conversation_id 直接拉即得全群历史（含入群前，决策⑥全量可读）。鉴权在工具侧按群成员资格前置，
        非成员根本走不到这里。附件在 content jsonb 内随行返回（别丢 attachments）。
        """
        lim = _clamp_limit(limit)
        cursor_id = _parse_cursor(cursor)
        conv = (conversation_id or '').strip() or None
        if conv is None:
            return {'messages': [], 'has_more': False, 'next_cursor': None}

        rows = (
            (
                await db.execute(
                    sa.text(
                        """
                    SELECT id,
                           conversation_id::text AS conversation_id,
                           from_id,
                           hasn_id,
                           sender_hasn_id,
                           content_type,
                           content,
                           created_time
                    FROM public.hasn_messages
                    WHERE conversation_id = CAST(:conv AS uuid)
                      AND (CAST(:cursor_id AS bigint) IS NULL OR id < CAST(:cursor_id AS bigint))
                    ORDER BY id DESC
                    LIMIT :limit
                    """
                    ),
                    {'conv': conv, 'cursor_id': cursor_id, 'limit': lim + 1},
                )
            )
            .mappings()
            .all()
        )

        has_more = len(rows) > lim
        page = rows[:lim]
        messages = [
            {
                'message_id': str(r['id']),
                'conversation_id': r['conversation_id'],
                'from': _sender_of(r),
                'content_type': r['content_type'],
                'content': _preview(r['content']),
                'attachments': _attachments_of(r['content']),
                'created_at': str(r['created_time']),
            }
            for r in page
        ]
        next_cursor = str(page[-1]['id']) if page and has_more else None
        return {'messages': messages, 'has_more': has_more, 'next_cursor': next_cursor}

    async def list_conversations(
        self,
        db: AsyncSession,
        owner_id: str,
        *,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """会话列表：按 owner 视角聚合会话，最后活动（最后一条消息 id）倒序 + keyset 翻页。

        以 owner 的 hasn_messages 为准聚合（含分身会话的 owner_copy），比查 hasn_conversations.participant
        更贴合「主人透明」——分身与外部 peer 的会话 owner 并非参与方，但主人应看得到。
        """
        lim = _clamp_limit(limit)
        cursor_id = _parse_cursor(cursor)

        rows = (
            (
                await db.execute(
                    sa.text(
                        """
                    WITH last_msgs AS (
                        SELECT DISTINCT ON (conversation_id)
                               conversation_id,
                               id,
                               from_id,
                               hasn_id,
                               sender_hasn_id,
                               content,
                               created_time
                        FROM public.hasn_messages
                        WHERE owner_id = :owner_id
                        ORDER BY conversation_id, id DESC
                    )
                    SELECT lm.conversation_id::text AS conversation_id,
                           lm.id AS last_id,
                           lm.from_id,
                           lm.hasn_id,
                           lm.sender_hasn_id,
                           lm.content,
                           lm.created_time,
                           c.type AS conv_type,
                           c.group_name,
                           c.participant_a_id,
                           c.participant_b_id,
                           c.member_count
                    FROM last_msgs lm
                    LEFT JOIN public.hasn_conversations c ON c.id = lm.conversation_id
                    WHERE (CAST(:cursor_id AS bigint) IS NULL OR lm.id < CAST(:cursor_id AS bigint))
                    ORDER BY lm.id DESC
                    LIMIT :limit
                    """
                    ),
                    {'owner_id': owner_id, 'cursor_id': cursor_id, 'limit': lim + 1},
                )
            )
            .mappings()
            .all()
        )

        has_more = len(rows) > lim
        page = rows[:lim]
        conversations = [
            {
                'conversation_id': r['conversation_id'],
                'type': r['conv_type'] or 'direct',
                'title': r['group_name'] or '',
                'participant_a_id': r['participant_a_id'],
                'participant_b_id': r['participant_b_id'],
                'member_count': r['member_count'],
                'last_message_id': str(r['last_id']),
                'last_message_from': _sender_of(r),
                'last_message': _preview(r['content'], cap=_CONVERSATION_PREVIEW_CAP),
                'last_message_at': str(r['created_time']),
            }
            for r in page
        ]
        next_cursor = str(page[-1]['last_id']) if page and has_more else None
        return {'conversations': conversations, 'has_more': has_more, 'next_cursor': next_cursor}

    async def search_messages(
        self,
        db: AsyncSession,
        owner_id: str,
        query: str,
        *,
        limit: int = _DEFAULT_LIMIT,
        cursor: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """关键词搜消息（owner 隔离，可选限定会话），倒序 + keyset 翻页。

        在文本 body.text 与卡片 title/description 上做 ILIKE 大小写不敏感匹配（不做全 jsonb 结构匹配，
        避免命中结构键误报）。query 里的 %/_ 做转义，防通配注入。
        """
        lim = _clamp_limit(limit)
        cursor_id = _parse_cursor(cursor)
        conv = (conversation_id or '').strip() or None
        q = (query or '').strip()
        if not q:
            return {'messages': [], 'has_more': False, 'next_cursor': None, 'query': query}
        escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f'%{escaped}%'

        rows = (
            (
                await db.execute(
                    sa.text(
                        r"""
                    SELECT id,
                           conversation_id::text AS conversation_id,
                           from_id,
                           hasn_id,
                           sender_hasn_id,
                           content_type,
                           content,
                           created_time
                    FROM public.hasn_messages
                    WHERE owner_id = :owner_id
                      AND (CAST(:conv AS text) IS NULL OR conversation_id = CAST(:conv AS uuid))
                      AND (CAST(:cursor_id AS bigint) IS NULL OR id < CAST(:cursor_id AS bigint))
                      AND (
                            COALESCE(content->>'text', '') ILIKE :pattern ESCAPE '\'
                         OR COALESCE(content->>'title', '') ILIKE :pattern ESCAPE '\'
                         OR COALESCE(content->>'description', '') ILIKE :pattern ESCAPE '\'
                      )
                    ORDER BY id DESC
                    LIMIT :limit
                    """
                    ),
                    {
                        'owner_id': owner_id,
                        'conv': conv,
                        'cursor_id': cursor_id,
                        'pattern': pattern,
                        'limit': lim + 1,
                    },
                )
            )
            .mappings()
            .all()
        )

        has_more = len(rows) > lim
        page = rows[:lim]
        messages = [
            {
                'message_id': str(r['id']),
                'conversation_id': r['conversation_id'],
                'from': _sender_of(r),
                'content_type': r['content_type'],
                'content': _preview(r['content']),
                'created_at': str(r['created_time']),
            }
            for r in page
        ]
        next_cursor = str(page[-1]['id']) if page and has_more else None
        return {'messages': messages, 'has_more': has_more, 'next_cursor': next_cursor, 'query': q}


agent_message_read_service = AgentMessageReadService()
