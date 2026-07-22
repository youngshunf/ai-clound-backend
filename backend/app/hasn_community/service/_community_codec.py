"""社区纯逻辑 codec —— 引用卡片派生/规范化/呈现 + 摘要 + 可见性守卫（候选④ god-file 拆分 community slice-1）。

从 `community_service.py` 抽出的**纯逻辑 + 纯数据**（无 self / 无 db / 无 await）：服务端按 (type,id,metadata)
派生 `hasn://` 跳转 URI（不信任客户端 uri，杜绝注入）、引用卡片规范化与按 viewer 呈现（作者可跳转、
他人只看静态卡）、安全摘要截断、Agent 读社区资源可见性守卫。`CommunityService` 各方法仍按原名 import 调用，
**零行为变化**；好处是这层安全敏感的纯逻辑（含受 `hasn://` 客户端无关铁律约束的 URI 派生）可独立单测。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.common.exception import errors

if TYPE_CHECKING:
    from backend.common.dataclasses import AgentTokenPayload

# ==================== 引用卡片（reference_cards）====================
# 社区文章/帖子可引用 Agent 技能 / 任务结果 / 聊天摘要，沿用 IM 卡片消息 HasnCardResource 形状。
# 被引用资源是「本地 daemon 资源」（本地 ULID id），云端不持有也无法对该 id 做归属查表，
# 故云端只能 authoritative 把控以下三件事，归属由三层保证：
#   1) 选择器只列出本人资源；2) 序列化时非作者不下发跳转 action（见 _present_reference_cards）；
#   3) 点击时由目标页/daemon 对真实本地资源二次鉴权。
ALLOWED_REFERENCE_TYPES = frozenset({'agent_skill', 'task_result', 'chat_summary'})
MAX_REFERENCE_CARDS = 10
_MAX_REF_TITLE_LEN = 200
_MAX_REF_SUMMARY_LEN = 500


def _build_reference_uri(card_type: str, resource_id: str, metadata: dict[str, Any]) -> str | None:
    """服务端按 (type, id, metadata) 派生 hasn:// 跳转 URI（不信任客户端 uri，杜绝注入）。"""
    if card_type == 'task_result':
        return f'hasn://tasks/sessions/{resource_id}'
    if card_type == 'chat_summary':
        base = f'hasn://messages/c/{resource_id}'
        message_id = metadata.get('message_id')
        return f'{base}#{message_id}' if message_id else base
    if card_type == 'agent_skill':
        agent_hasn_id = metadata.get('agent_hasn_id')
        if not agent_hasn_id:
            return None
        return f'hasn://agents/{agent_hasn_id}/skills?skill={resource_id}'
    return None


def _normalize_reference_cards(
    raw: Any,
    *,
    author_hasn_id: str,
) -> list[dict[str, Any]]:
    """
    规范化并校验引用卡片，存储为 HasnCardResource 形状。

    - 校验 type ∈ 允许集合、id 非空；超过 MAX_REFERENCE_CARDS 截断
    - URI 由服务端派生（_build_reference_uri），忽略客户端传入的 uri
    - access 由服务端盖章：跳转恒 author_only，readable_by = [作者]
    - title/summary 为展示用注解，长度截断（XSS 由前端渲染层转义）
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise errors.RequestError(msg='reference_cards 必须是数组')

    normalized: list[dict[str, Any]] = []
    for item in raw[:MAX_REFERENCE_CARDS]:
        if not isinstance(item, dict):
            raise errors.RequestError(msg='引用卡片必须是对象')
        card_type = item.get('type')
        resource_id = item.get('id')
        if card_type not in ALLOWED_REFERENCE_TYPES:
            raise errors.RequestError(msg=f'非法引用卡片类型：{card_type}')
        if not resource_id or not isinstance(resource_id, str):
            raise errors.RequestError(msg='引用卡片缺少 id')
        raw_metadata = item.get('metadata')
        metadata = (
            {key: value for key, value in raw_metadata.items() if isinstance(key, str)}
            if isinstance(raw_metadata, dict)
            else {}
        )
        uri = _build_reference_uri(card_type, resource_id, metadata)
        if not uri:
            raise errors.RequestError(msg=f'引用卡片缺少必要字段（{card_type} 需 metadata.agent_hasn_id）')
        normalized.append({
            'type': card_type,
            'id': resource_id,
            'uri': uri,
            'title': str(item.get('title') or '')[:_MAX_REF_TITLE_LEN],
            'summary': str(item.get('summary') or '')[:_MAX_REF_SUMMARY_LEN],
            'access': {'visibility': 'author_only', 'readable_by': [author_hasn_id]},
            'metadata': metadata,
        })
    return normalized


def _present_reference_cards(
    stored: Any,
    viewer_hasn_id: str | None,
) -> list[dict[str, Any]]:
    """
    序列化引用卡片供展示。仅当 viewer ∈ access.readable_by（即作者本人）时下发可跳转 action；
    其他 viewer 只得到静态卡片，且 uri 完全不下发——实现「发布者可跳转、其他人只看卡片」。
    """
    if not stored or not isinstance(stored, list):
        return []
    presented: list[dict[str, Any]] = []
    for card in stored:
        if not isinstance(card, dict):
            continue
        access = card.get('access') or {}
        readable_by = access.get('readable_by') or []
        can_open = bool(viewer_hasn_id) and viewer_hasn_id in readable_by
        item: dict[str, Any] = {
            'type': card.get('type'),
            'id': card.get('id'),
            'title': card.get('title') or '',
            'summary': card.get('summary') or '',
            'metadata': card.get('metadata') or {},
        }
        if can_open and card.get('uri'):
            item['action'] = {'kind': 'open_uri', 'uri': card['uri']}
        presented.append(item)
    return presented


# ==================== 媒体（media_json：帖子图片/视频九宫格）====================
# 社区帖子是公开内容（含 /community/open/* 无鉴权只读），所以媒体走公开 CDN URL
# （由 daemon 代理 /sys/upload/{image,video} 落公共桶回稳定链接），**不**走聊天那套按查看者
# ACL 的私有 hasn://asset 通道。存储形状 media_json = {"items": [{type,url,...}]}（JSONB 需 dict）。
# 文章媒体走正文内联（content 内的 <img>/<video>），不用 media_json；此 codec 仅服务帖子。
ALLOWED_MEDIA_TYPES = frozenset({'image', 'video'})
MAX_MEDIA_ITEMS = 9  # 朋友圈式九宫格
_MEDIA_INT_FIELDS = ('width', 'height', 'duration_ms')
_MAX_MEDIA_URL_LEN = 1000


def _normalize_media(raw: Any) -> dict[str, Any]:
    """
    规范化并校验帖子媒体，存储为 {"items": [...]}（JSONB 需 dict，故包一层 items）。

    - 每项 type ∈ {image, video}、url 为非空 http(s) 字符串；超过 MAX_MEDIA_ITEMS 截断
    - 仅保留白名单字段：type/url + 可选 width/height/duration_ms/poster（视频封面帧）
    - 非法项（缺 url / 非法 type）直接跳过，绝不让发帖因单张图坏掉而整体失败
    - width/height/duration_ms 只接受非负整数，poster 只接受 http(s) 字符串
    """
    if not raw:
        return {}
    if not isinstance(raw, list):
        raise errors.RequestError(msg='media 必须是数组')

    items: list[dict[str, Any]] = []
    for entry in raw[:MAX_MEDIA_ITEMS]:
        if not isinstance(entry, dict):
            continue
        media_type = entry.get('type')
        url = entry.get('url')
        if media_type not in ALLOWED_MEDIA_TYPES:
            continue
        if not url or not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            continue
        item: dict[str, Any] = {'type': media_type, 'url': url[:_MAX_MEDIA_URL_LEN]}
        for field in _MEDIA_INT_FIELDS:
            value = entry.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                item[field] = value
        poster = entry.get('poster')
        if isinstance(poster, str) and poster.startswith(('http://', 'https://')):
            item['poster'] = poster[:_MAX_MEDIA_URL_LEN]
        items.append(item)
    return {'items': items} if items else {}


def _present_media(stored: Any) -> list[dict[str, Any]]:
    """序列化帖子媒体供展示：从存储的 {"items": [...]} 取出扁平列表（缺/坏则空数组）。"""
    if not isinstance(stored, dict):
        return []
    items = stored.get('items')
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get('url')]


def _assert_agent_can_read_community_resource(*, agent: AgentTokenPayload, resource: Any) -> None:
    visibility = getattr(resource, 'visibility', 'public')
    if visibility == 'public':
        return
    owner_hasn_id = getattr(resource, 'owner_hasn_id', None)
    author_hasn_id = getattr(resource, 'author_hasn_id', None)
    if agent.owner_hasn_id in {owner_hasn_id, author_hasn_id}:
        return
    raise errors.ForbiddenError(msg='社区资源不可见')


def _safe_summary(content: str | None, *, limit: int = 160) -> str:
    text = ' '.join((content or '').split())
    if len(text) <= limit:
        return text
    return f'{text[:limit].rstrip()}...'
