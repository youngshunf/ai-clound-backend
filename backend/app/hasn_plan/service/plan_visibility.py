"""规划应用·企业事件可见性两轴（PLAN-ENT A3，[04] §4.2）。

可见性是**两轴**：
- **WHO（数据范围档）**：决定「能看到谁的日程」——由 [02] §5.2 数据范围解析器给出可见成员集合，
  在 service 查询层前置过滤（`plan_app_service` 调用），本模块不承载 WHO。
- **WHAT（本模块）**：对一条「看得到」的企业事件，决定露多细——`full`（全详情）或 `busy`（仅忙闲块）。
  规则（[04] §4.2，隐私默认只露忙闲）：
    · 自己的事件（`owner_hasn_id == viewer`）→ full；
    · 我是参会人（`event_attendee`）→ full（会议对参会人天然全透明，不经可见性列）；
    · `event.visibility == 'public'`（企业公开）→ full；
    · `resource_share` 授予我 / 我的角色 → full；
    · 否则（数据范围内的同事私有事件）→ **busy**（隐藏标题/推理/来源等，仅留时间块占用）。
  数据范围**外**的事件由 WHO 轴直接不返回（连「忙」都不露，[04] PE-D2），不进本模块。

纯函数、零 I/O、零 mock：入参是已由上层查得的布尔事实（is_attendee/is_shared）与事件 dict，
便于单测穷举，也让 service 查询层保持「查事实 → 判可见性」的清晰分层。
"""

from __future__ import annotations

from typing import Any, Literal

DetailLevel = Literal['full', 'busy']

# busy 投影保留的「时间块占用」字段（供日历忙闲格渲染）；其余描述性字段一律隐去。
_BUSY_KEEP_KEYS: frozenset[str] = frozenset({
    'id',
    'owner_hasn_id',
    'enterprise_id',
    'dept_id',
    'start_at',
    'end_at',
    'all_day',
    'kind',
})
_BUSY_TITLE = '忙碌'


def event_detail_level(
    event: dict[str, Any],
    *,
    viewer_hasn_id: str,
    is_attendee: bool = False,
    is_shared: bool = False,
) -> DetailLevel:
    """判定一条企业事件对 viewer 露多细：``full``（全详情）或 ``busy``（仅忙闲）。

    入参 is_attendee / is_shared 由上层（service）据 event_attendee / resource_share 查得后传入。
    个人事件（``enterprise_id`` 为 None）不应进入本判定——个人路径全 full（[04] 不变量 #1）。
    """
    if event.get('owner_hasn_id') == viewer_hasn_id:
        return 'full'
    if is_attendee:
        return 'full'
    if event.get('visibility') == 'public':
        return 'full'
    if is_shared:
        return 'full'
    return 'busy'


def redact_event_to_busy(event: dict[str, Any]) -> dict[str, Any]:
    """把事件裁成「仅忙闲块」：保留时间块占用字段，隐去标题/推理/来源等隐私内容。

    返回带 ``busy=True`` / ``redacted=True`` 标记 + 占位标题 ``忙碌``，供前端渲染匿名忙块。
    """
    out: dict[str, Any] = {k: event[k] for k in _BUSY_KEEP_KEYS if k in event}
    out['title'] = _BUSY_TITLE
    out['busy'] = True
    out['redacted'] = True
    return out


def apply_event_visibility(
    event: dict[str, Any],
    *,
    viewer_hasn_id: str,
    is_attendee: bool = False,
    is_shared: bool = False,
) -> dict[str, Any]:
    """按 WHAT 轴裁剪一条已「看得到」的企业事件：full 原样、busy 裁成忙闲块。"""
    if event_detail_level(event, viewer_hasn_id=viewer_hasn_id, is_attendee=is_attendee, is_shared=is_shared) == 'full':
        return event
    return redact_event_to_busy(event)
