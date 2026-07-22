"""会议副驾会议资源的 G6 统一资源权限门·资源适配器（doc32 §4）。

会议是 owner 私有的一等结果资源，可显式分享给联系人（`POST /meetings/{id}/share` →
通用 `hasn_resource_share`）。分享建行前，平台 `resource_share_service.upsert_share` 会
**fail-closed** 校验 `resource_type='meeting'` 已注册本适配器（「能分享、必能判」）——故本模块
必须在应用启动时被 import 并 `register()`，否则会议分享会被安全默认挡下。

resource_type='meeting'，leaf id = 会议 UUID（= hasn://meeting/{id} 的 {id} 段）；
会议自身即 share 主体，无父链、无维度②域限制、无内嵌私有资产收集（升格媒体走 shared_media_json，
不经资产投影门）。

层次纪律（doc32 §3）：适配器属应用层，可 import 本应用模型；平台门永不反向 import 本应用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn.service.authz.resource_registry import ResourceMeta, resource_kind_registry
from backend.app.hasn_copilot.model import Meetings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _to_uuid(resource_id: str) -> UUID | None:
    """把权威 id 串转 UUID；畸形返回 None（门据此 404，绝不冒 500）。"""
    try:
        return UUID(str(resource_id))
    except (TypeError, ValueError):
        return None


class MeetingResourceAdapter:
    """会议资源适配器：resource_type='meeting'，leaf id = 会议 UUID，自身即 share 主体（无父链）。"""

    resource_type = 'meeting'
    # 会议副驾无 Agent 调用的 hasn.* MCP 工具（走工作会话派发），故无工具入参别名需登记。
    id_param_aliases: tuple[str, ...] = ()

    async def load_meta(self, db: AsyncSession, resource_id: str) -> ResourceMeta | None:
        meeting_id = _to_uuid(resource_id)
        if meeting_id is None:
            return None
        meeting = (await db.execute(sa.select(Meetings).where(Meetings.id == meeting_id))).scalar_one_or_none()
        if meeting is None:
            return None
        # 会议首发恒个人归属、私有可见（enterprise_id 是团队协作预留，非 int 企业 id，判权按 None 处理）。
        return ResourceMeta(
            resource_id=str(meeting.id),
            owner_hasn_id=meeting.owner_hasn_id,
            owner_scope='personal',
            enterprise_id=None,
            visibility='private',
            row=meeting,
        )


def register() -> None:
    """把会议资源适配器注册进平台注册表（重名幂等跳过，进程级只应跑一次）。"""
    adapter = MeetingResourceAdapter()
    if adapter.resource_type not in resource_kind_registry.registered_types():
        resource_kind_registry.register(adapter)


# 模块导入即注册（idempotent guard 保证进程内只注册一次）。会议服务 / app API 模块 import 本模块，
# 且平台 ai_native_app_registry 也 import 本模块——任一路径先触发即完成注册。
register()
