import uuid

from typing import Any, Sequence

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agent_channel_mirrors import hasn_agent_channel_mirrors_dao
from backend.app.hasn.model import HasnAgentChannelMirrors, HasnHumans
from backend.app.hasn.schema.hasn_agent_channel_mirrors import (
    CreateHasnAgentChannelMirrorsParam,
    DeleteHasnAgentChannelMirrorsParam,
    UpdateHasnAgentChannelMirrorsParam,
    UpsertChannelMirrorRequest,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.common.security.secret_keys import safe_json

# bound_account_display 列宽 varchar(128)：上报值若超长则截断兜底，避免 StringDataRightTruncation。
_BOUND_DISPLAY_MAX = 128
# last_error 列宽 varchar(500)：同理兜底截断。
_LAST_ERROR_MAX = 500


async def _resolve_owner(db: AsyncSession, user_id: int) -> str:
    """三道隔离防线第①②道：从 JWT 的 user.id 解析 owner hasn_id（不取自 query/body）。

    `SELECT hasn_id FROM hasn_humans WHERE user_id=<jwt user.id>`；查不到说明当前用户未注册
    HASN 身份 → ForbiddenError（403）。返回的 owner_id 即后续 SQL 强制过滤键（第③道）。
    """
    owner_id = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))
    ).scalar_one_or_none()
    if not owner_id:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return owner_id


class HasnAgentChannelMirrorsService:
    @staticmethod
    async def list_for_owner(*, db: AsyncSession, user_id: int) -> dict[str, list[dict[str, Any]]]:
        """返回当前 owner 的跨设备渠道摘要列表（owner_id 由 JWT 解析，强制 WHERE owner_id）。"""
        owner_id = await _resolve_owner(db, user_id)
        rows = await hasn_agent_channel_mirrors_dao.list_by_owner(db, owner_id)
        items = [
            {
                'id': r.id,
                'mirror_id': r.mirror_id,
                'owner_id': r.owner_id,
                'agent_hasn_id': r.agent_hasn_id,
                'channel': r.channel,
                'origin_node_id': r.origin_node_id,
                'runtime_location': r.runtime_location,
                'status': r.status,
                'bound_account_display': r.bound_account_display,
                'metadata_json': r.metadata_json or {},
                'last_error': r.last_error,
                'created_time': r.created_time.isoformat() if r.created_time else None,
                'updated_time': r.updated_time.isoformat() if r.updated_time else None,
            }
            for r in rows
        ]
        return {'items': items}

    @staticmethod
    async def upsert_for_owner(
        *, db: AsyncSession, user_id: int, request: UpsertChannelMirrorRequest
    ) -> dict[str, Any]:
        """daemon 上报脱敏摘要：owner_id 由 JWT 覆盖；写库前过 safe_json 第④层兜底脱敏。"""
        owner_id = await _resolve_owner(db, user_id)

        # 第④层脱敏兜底（§8.5-④）：命中 SECRET_KEYS 或 _secret/_token 后缀的键被剔除。
        safe_metadata = safe_json(request.metadata_json or {})
        if not isinstance(safe_metadata, dict):  # safe_json 对非 dict 原样返回，强制收敛为 dict
            safe_metadata = {}

        bound_display = request.bound_account_display
        if bound_display is not None and len(bound_display) > _BOUND_DISPLAY_MAX:
            bound_display = bound_display[:_BOUND_DISPLAY_MAX]
        last_error = request.last_error
        if last_error is not None and len(last_error) > _LAST_ERROR_MAX:
            last_error = last_error[:_LAST_ERROR_MAX]

        row = await hasn_agent_channel_mirrors_dao.upsert_mirror(
            db,
            mirror_id=f'chm_{uuid.uuid4().hex}',
            owner_id=owner_id,
            agent_hasn_id=request.agent_hasn_id,
            channel=request.channel,
            origin_node_id=request.origin_node_id,
            runtime_location=request.runtime_location,
            status=request.status,
            bound_account_display=bound_display,
            metadata_json=safe_metadata,
            last_error=last_error,
        )
        return {
            'id': row.id,
            'mirror_id': row.mirror_id,
            'owner_id': row.owner_id,
            'agent_hasn_id': row.agent_hasn_id,
            'channel': row.channel,
            'origin_node_id': row.origin_node_id,
            'runtime_location': row.runtime_location,
            'status': row.status,
            'bound_account_display': row.bound_account_display,
            'metadata_json': row.metadata_json or {},
            'last_error': row.last_error,
            'created_time': row.created_time.isoformat() if row.created_time else None,
            'updated_time': row.updated_time.isoformat() if row.updated_time else None,
        }


    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAgentChannelMirrors:
        """
        获取HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param pk: HASN Agent 渠道脱敏摘要跨设备镜像 ID
        :return:
        """
        hasn_agent_channel_mirrors = await hasn_agent_channel_mirrors_dao.get(db, pk)
        if not hasn_agent_channel_mirrors:
            raise errors.NotFoundError(msg='HASN Agent 渠道脱敏摘要跨设备镜像不存在')
        return hasn_agent_channel_mirrors

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Agent 渠道脱敏摘要跨设备镜像列表

        :param db: 数据库会话
        :return:
        """
        hasn_agent_channel_mirrors_select = await hasn_agent_channel_mirrors_dao.get_select()
        return await paging_data(db, hasn_agent_channel_mirrors_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAgentChannelMirrors]:
        """
        获取所有HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :return:
        """
        hasn_agent_channel_mirrors_list = await hasn_agent_channel_mirrors_dao.get_all(db)
        return hasn_agent_channel_mirrors_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAgentChannelMirrorsParam) -> None:
        """
        创建HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param obj: 创建HASN Agent 渠道脱敏摘要跨设备镜像参数
        :return:
        """
        await hasn_agent_channel_mirrors_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAgentChannelMirrorsParam) -> int:
        """
        更新HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param pk: HASN Agent 渠道脱敏摘要跨设备镜像 ID
        :param obj: 更新HASN Agent 渠道脱敏摘要跨设备镜像参数
        :return:
        """
        count = await hasn_agent_channel_mirrors_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAgentChannelMirrorsParam) -> int:
        """
        删除HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param obj: HASN Agent 渠道脱敏摘要跨设备镜像 ID 列表
        :return:
        """
        count = await hasn_agent_channel_mirrors_dao.delete(db, obj.pks)
        return count


hasn_agent_channel_mirrors_service: HasnAgentChannelMirrorsService = HasnAgentChannelMirrorsService()
