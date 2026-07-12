"""plan（规划应用）的 G6 资源类型适配器（doc32 §4·doc33 S3-1）。

plan 的**大多数**资源（目标 goal / 计划 plan / 待办 todo / 里程碑 / 习惯）是 **owner 私有、不跨
owner 分享**：分身只动自有主人的资源，service 层一律 owner-key（`_get_*(owner=..., pk=...)`）判权，
G6 无须为它们建 adapter。**唯一走 `hasn_resource_share` 跨 owner 显式分享的资源是日程事件 `event`**
（resource_type=`plan_event`，见 `plan_authz.py` 的 `_shared_plan_event_ids`：human / enterprise /
role 三类授予）。本模块只为 **plan_event** 这一种资源贡献 adapter。

**可见性映射（关键·不动语义）**：`Event` 表无 `owner_scope` 列、可见语义靠 `enterprise_id` +
`visibility`（`private`/`public`）表达（见 `plan_authz` / `plan_visibility`）。为让平台内核
`resolve_effective_permission`（只认 owner_scope/visibility/enterprise_id）判出与 plan 现有语义**一致**
的档位，`load_meta` 把行状态翻译成内核可读的可见性：

- **owner_scope 恒 `personal`**：plan 事件归属**个人**（即便企业事件也由组织者个人持有），故**绝不**把
  owner_scope 置 `enterprise`——否则会触发内核 `admin_grant`（企业 owner/admin → manager），与 plan
  语义相悖（plan_authz 里企业 admin 数据范围虽为 `all`，但对他人**私有**事件只露忙闲 `busy`、绝不授
  full/编辑）。owner 本人恒经 `owner_grant` 得 manager，与 owner-key 一致。
- **企业公开事件（`enterprise_id` 非空且 `visibility=='public'`）→ `visibility='enterprise'`**：内核对
  「同企业成员 + enterprise 可见」授基线 viewer（复刻 plan「企业公开事件对全体成员 full 可读」）。
- **其余（个人事件 / 企业私有事件）→ `visibility='private'`**：仅 `owner_grant` 或**显式 share** 生效
  （非 owner、未被 share 的成员 → 内核判 none → 门 404）。企业私有事件对同事的「忙闲块」旁路是
  `event.list`/`availability` 区间读自带的 WHAT 裁剪（`plan_visibility`），**不经**本门（本门只判
  「按 id 读写某个具体事件」这一面），二者不冲突。

`resource_id` 逐字用 `str(event.id)`——与 `plan_authz._shared_plan_event_ids`（`int(r)` 反解 share
的 `resource_id`）同一口径。`Event` 无软删列（`delete_event` 为硬删），`load_meta` 只按 `id` 取行。

**`id_param_aliases` 为何留空（`()`）**：守卫 1（`test_resource_access_declaration_contract`）据别名集
机械匹配「工具入参命中别名却漏声明」。plan 事件在工具入参里的惯用名是 `event_id`，但用它做别名会连坐
**两类语义迥异**的工具：`event.invite`（组织者=owner-key，可安全声明 editor）与 `event.rsvp`（受邀者
按 `event_attendee` 回执、**非** resource_share 授权，viewer/editor/manager 三档皆判不出、加声明必破受邀
回执流）。而 `event.rsvp` 属守卫的「真例外」（刻意不按 resource_share 判），须登记进那份契约白名单——
本期该文件不在可改范围。另一候选别名 `id` 又过于泛化（goal/todo/plan/habit/milestone 全用 `id`，做别名
会误伤一片）。故本期**只注册 adapter、不挂工具声明、别名留空**：plan_event 的跨 owner share 目前只被
区间读工具（各自带可见性裁剪）消费，**没有**任何「按 id 读写具体事件且需消费跨 owner share」的分身工具
（update/delete/invite 皆 owner-key、rsvp 走 attendee）。adapter 仍注册以供 registry 完整性（S3-3 seed）、
建 share 行运行时 fail-closed 校验（S2-5）与将来门用。后续若契约白名单可改，应把别名收敛为 `('event_id',)`、
给 `event.invite` 声明 editor、并把 `event.rsvp` 的 `event_id` 登记为真例外。

层次纪律（doc32 §3）：适配器属应用层，可 import 本应用模型；平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn_plan.model import Event
from backend.app.hasn_plan.service.plan_authz import PLAN_EVENT_RESOURCE_TYPE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _to_int(resource_id: str) -> int | None:
    """把权威 id 串转 int；畸形返回 None（门据此 404，绝不冒 500）。"""
    try:
        return int(resource_id)
    except (TypeError, ValueError):
        return None


class PlanEventResourceAdapter:
    """plan 日程事件资源适配器：resource_type='plan_event'，leaf id = event.id（自身即 share 主体）。"""

    resource_type = PLAN_EVENT_RESOURCE_TYPE  # 'plan_event'——与 plan_authz / resource_share 逐字同串
    # 别名留空：见模块 docstring「`id_param_aliases` 为何留空」（event_id 会连坐 rsvp、id 过泛化）。
    id_param_aliases: tuple[str, ...] = ()

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        event_id = _to_int(resource_id)
        if event_id is None:
            return None
        ev = (await db.execute(sa.select(Event).where(Event.id == event_id))).scalar_one_or_none()
        if ev is None:
            return None
        # 见模块 docstring「可见性映射」：owner_scope 恒 personal（不触发 admin_grant）；企业公开事件
        # → enterprise 可见（成员基线 viewer），其余（个人/企业私有）→ private（仅 owner + 显式 share）。
        visibility = 'enterprise' if ev.enterprise_id is not None and ev.visibility == 'public' else 'private'
        return ResourceMeta(
            resource_id=str(ev.id),
            owner_hasn_id=ev.owner_hasn_id,
            owner_scope='personal',
            enterprise_id=ev.enterprise_id,
            visibility=visibility,
            row=ev,
        )


def register() -> None:
    """把 plan_event 资源适配器注册进平台注册表（重名即抛，进程级只应跑一次）。"""
    adapter = PlanEventResourceAdapter()
    if adapter.resource_type not in resource_kind_registry.registered_types():
        resource_kind_registry.register(adapter)


# 模块导入即注册（模块缓存保证进程内只跑一次）。注意：权威启动注册器 `ai_native_app_registry` 目前
# **未** import 本模块（本期不在可改范围），故运行时注册依赖 plan 平台工具模块 `app/mcp/tools/plan.py`
# 的「使用点注册兜底」import（该模块在 MCP server 启动装配工具时被加载）——与 deck/knowledge 的
# 使用点兜底同范式。
register()
