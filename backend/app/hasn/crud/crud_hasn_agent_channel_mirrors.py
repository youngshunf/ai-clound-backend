from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAgentChannelMirrors
from backend.app.hasn.schema.hasn_agent_channel_mirrors import (
    CreateHasnAgentChannelMirrorsParam,
    UpdateHasnAgentChannelMirrorsParam,
)
from backend.utils.timezone import timezone


class CRUDHasnAgentChannelMirrors(CRUDPlus[HasnAgentChannelMirrors]):
    @staticmethod
    async def list_by_owner(db: AsyncSession, owner_id: str) -> Sequence[HasnAgentChannelMirrors]:
        """列某 owner 的全部跨设备渠道摘要镜像（强制 WHERE owner_id，第三道隔离防线）。

        按 updated_time 倒序（最近上报在前），与索引 idx_..._owner(owner_id, updated_time DESC) 对齐。
        """
        stmt = (
            select(HasnAgentChannelMirrors)
            .where(HasnAgentChannelMirrors.owner_id == owner_id)
            .order_by(HasnAgentChannelMirrors.updated_time.desc(), HasnAgentChannelMirrors.id.desc())
        )
        return (await db.execute(stmt)).scalars().all()

    @staticmethod
    async def upsert_mirror(
        db: AsyncSession,
        *,
        mirror_id: str,
        owner_id: str,
        agent_hasn_id: str,
        channel: str,
        origin_node_id: str,
        runtime_location: str,
        status: str,
        bound_account_display: str | None,
        metadata_json: dict,
        last_error: str | None,
    ) -> HasnAgentChannelMirrors:
        """按唯一键 (owner_id, agent_hasn_id, channel, origin_node_id) 做时间择新 upsert。

        ON CONFLICT DO UPDATE 带 ``WHERE 旧 updated_time < EXCLUDED.updated_time``：
        旧值不覆盖新值（最新上报优先，设计 §5.5/§6.2）。冲突且不满足 WHERE 时不更新，
        随后回查取库内当前权威行返回（保证返回值始终是库内最新态，而非本次未生效的入参）。
        owner_id 已由 service 层以 JWT 解析覆盖，crud 不再二次校验归属。
        """
        now = timezone.now()
        insert_stmt = pg_insert(HasnAgentChannelMirrors).values(
            mirror_id=mirror_id,
            owner_id=owner_id,
            agent_hasn_id=agent_hasn_id,
            channel=channel,
            origin_node_id=origin_node_id,
            runtime_location=runtime_location,
            status=status,
            bound_account_display=bound_account_display,
            metadata_json=metadata_json,
            last_error=last_error,
            created_time=now,
            updated_time=now,
        )
        stmt = insert_stmt.on_conflict_do_update(
            constraint='uq_hasn_agent_channel_mirrors_scope',
            set_={
                'runtime_location': insert_stmt.excluded.runtime_location,
                'status': insert_stmt.excluded.status,
                'bound_account_display': insert_stmt.excluded.bound_account_display,
                'metadata_json': insert_stmt.excluded.metadata_json,
                'last_error': insert_stmt.excluded.last_error,
                'updated_time': insert_stmt.excluded.updated_time,
            },
            where=HasnAgentChannelMirrors.updated_time < insert_stmt.excluded.updated_time,
        )
        await db.execute(stmt)
        await db.flush()
        # 回查库内当前权威行（DO UPDATE WHERE 未命中时入参不生效，需以库内真实态为准）。
        return (
            await db.execute(
                select(HasnAgentChannelMirrors).where(
                    HasnAgentChannelMirrors.owner_id == owner_id,
                    HasnAgentChannelMirrors.agent_hasn_id == agent_hasn_id,
                    HasnAgentChannelMirrors.channel == channel,
                    HasnAgentChannelMirrors.origin_node_id == origin_node_id,
                )
            )
        ).scalar_one()

    async def get(self, db: AsyncSession, pk: int) -> HasnAgentChannelMirrors | None:
        """
        获取HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param pk: HASN Agent 渠道脱敏摘要跨设备镜像 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Agent 渠道脱敏摘要跨设备镜像列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAgentChannelMirrors]:
        """
        获取所有HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAgentChannelMirrorsParam) -> None:
        """
        创建HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param obj: 创建HASN Agent 渠道脱敏摘要跨设备镜像参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAgentChannelMirrorsParam) -> int:
        """
        更新HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param pk: HASN Agent 渠道脱敏摘要跨设备镜像 ID
        :param obj: 更新 HASN Agent 渠道脱敏摘要跨设备镜像参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Agent 渠道脱敏摘要跨设备镜像

        :param db: 数据库会话
        :param pks: HASN Agent 渠道脱敏摘要跨设备镜像 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_agent_channel_mirrors_dao: CRUDHasnAgentChannelMirrors = CRUDHasnAgentChannelMirrors(HasnAgentChannelMirrors)
