"""金融投研 6 类产物 + watchlist 的 `:sync` 同事务核心（05 §5.3a / §5.5）。

本地优先第三条范式（05 §5.1）：daemon 侧业务本体先落本地 SQLite，云端写只是可重试 outbox 的
**投影**。因此这里的登记不能走 best-effort 的 `register_app_resource_artifact`——必须在**同一 PG
事务**内直接调用会抛错的 `HasnArtifactsService.record_app_resource_artifact`，任一步失败整体回滚
5xx，由本地 outbox 幂等重推（05 §1.2 严格登记例外）。

同事务契约（每个产物 `:sync` 端点，一个 PG 事务内）：
  ① owner 只取鉴权上下文（客户端传入的 owner 不可信）；按 op_id/base_revision 幂等回放或乐观锁校验
  ② create：按 (owner_id, local_ref) 幂等铸行；update/delete：按 (owner_id, server_id) 定位
     → 有效新写 revision + 1，last_client_op_id = op_id，RETURNING id, revision
  ③ 拿到 server_id → descriptor.build_uri(server_id)
  ④ descriptor = registry.resource_descriptor('finance', resource_kind)
  ⑤ create/update → record_app_resource_artifact(...)；delete → strict 软删该资源全部 active 指针
     两路异常都必须向外抛 → commit → 返回 {id, revision}

watchlist 是人工维护的自选股（非产物），只做版本闸/UPSERT/tombstone，**不登记**（05 §3.1.1）。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core.app_platform import ai_native_app_registry
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.hasn_finance.service.finance_read_service import serialize_resource_detail
from backend.common.exception import errors
from backend.common.log import log

# 本应用 app_id（URL /api/v1/finance/*，schema hasn_finance）
_APP_ID = 'finance'


class FinanceSyncService:
    """6 类产物 + watchlist 的 `:sync` 同事务同步核心。"""

    # ---------------------------------------------------------------- 产物同步

    @classmethod
    async def sync_product(
        cls,
        db: AsyncSession,
        *,
        model_cls: Any,
        resource_kind: str,
        owner_id: str,
        op: str,
        op_id: str,
        base_revision: int | None,
        local_ref: str | None,
        server_id: str | None,
        fields: dict[str, Any],
        node_id: str | None = None,
        agent_hasn_id: str | None = None,
        session_id: str | None = None,
        project_id: str | None = None,
        title: str = '',
        summary: str | None = None,
        source_tool: str | None = None,
    ) -> dict[str, Any]:
        """产物 `:sync` 同事务核心。返回 {'id': str, 'revision': int, 'op': str}。

        - `op` ∈ {'create','update','delete'}
        - `fields` 是本次要写入 / 更新的业务列（端点层已做 FK 跨 owner 校验、隐私列剔除）
        - `agent_hasn_id` 为空（主人手建）→ 业务行照写，但**跳过**登记（register-on-write 判据「分身参与」）
        """
        if op == 'create':
            row, replayed = await cls._create_or_replay(
                db,
                model_cls=model_cls,
                owner_id=owner_id,
                op_id=op_id,
                local_ref=local_ref,
                node_id=node_id,
                agent_hasn_id=agent_hasn_id,
                fields=fields,
            )
        elif op in ('update', 'delete'):
            row, replayed = await cls._locate_and_apply(
                db,
                model_cls=model_cls,
                owner_id=owner_id,
                op=op,
                op_id=op_id,
                base_revision=base_revision,
                server_id=server_id,
                fields=fields,
            )
        else:
            raise errors.RequestError(msg=f'非法 op：{op}（仅 create/update/delete）')

        server_id_out = str(row.id)
        # 幂等回放（op_id 已应用过）时不重复登记——登记本身幂等（UPSERT），但省一次无谓写。
        if not replayed:
            if op == 'delete':
                await cls._unregister_product(db, owner_id=owner_id, resource_kind=resource_kind, server_id=server_id_out)
            else:
                await cls._register_product(
                    db,
                    resource_kind=resource_kind,
                    server_id=server_id_out,
                    agent_hasn_id=agent_hasn_id,
                    owner_id=owner_id,
                    session_id=session_id,
                    project_id=project_id,
                    title=title,
                    summary=summary,
                    source_tool=source_tool,
                )
        return {'id': server_id_out, 'revision': int(row.revision), 'op': op}

    @classmethod
    async def _create_or_replay(
        cls,
        db: AsyncSession,
        *,
        model_cls: Any,
        owner_id: str,
        op_id: str,
        local_ref: str | None,
        node_id: str | None,
        agent_hasn_id: str | None,
        fields: dict[str, Any],
    ) -> tuple[Any, bool]:
        """create：按 (owner_id, local_ref) 幂等铸行。已存在同 local_ref → 幂等回放（不重复插入）。"""
        if not local_ref:
            raise errors.RequestError(msg='create 必须携带 local_ref（本地幂等键）')
        existing = (
            await db.execute(
                select(model_cls).where(model_cls.owner_id == owner_id, model_cls.local_ref == local_ref).limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 幂等回放：同一 local_ref 的 create 重放（响应丢失重发），返回既有行、不重复铸。
            return existing, True
        row = model_cls(
            owner_id=owner_id,
            agent_hasn_id=agent_hasn_id,
            local_ref=local_ref,
            node_id=node_id,
            revision=1,
            last_client_op_id=op_id,
            status='active',
            **fields,
        )
        db.add(row)
        await db.flush()
        return row, False

    @classmethod
    async def _locate_and_apply(
        cls,
        db: AsyncSession,
        *,
        model_cls: Any,
        owner_id: str,
        op: str,
        op_id: str,
        base_revision: int | None,
        server_id: str | None,
        fields: dict[str, Any],
    ) -> tuple[Any, bool]:
        """update/delete：按 (owner_id, server_id) 定位 + 幂等回放 + 乐观锁 + revision+1。"""
        if not server_id:
            raise errors.RequestError(msg=f'{op} 必须携带 server_id（云端权威 id）')
        try:
            pk = int(server_id)
        except (TypeError, ValueError):
            raise errors.RequestError(msg=f'server_id 非法：{server_id!r}')
        row = (
            await db.execute(
                select(model_cls).where(model_cls.owner_id == owner_id, model_cls.id == pk).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            # owner 隔离：定位不到（不存在 / 跨 owner）一律 404，不泄漏存在性。
            raise errors.NotFoundError(msg='资源不存在或无权访问')

        # 幂等回放：同一 op_id 已成功应用（响应丢失重发）→ 返回当前状态，不再 +1、不冲突。
        if row.last_client_op_id and row.last_client_op_id == op_id:
            return row, True

        # 乐观锁：base_revision 与云端当前 revision 不一致 = 有别的设备先改过 → 409 带服务端快照。
        if base_revision is not None and int(row.revision) != int(base_revision):
            raise errors.ConflictError(
                msg='版本冲突：云端已被其他设备更新，请据服务端快照重放',
                data={
                    'server_id': str(row.id),
                    'revision': int(row.revision),
                    'conflict': True,
                    'snapshot': serialize_resource_detail(row),
                },
            )

        if op == 'delete':
            row.status = 'deleted'
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        row.revision = int(row.revision) + 1
        row.last_client_op_id = op_id
        await db.flush()
        return row, False

    # ---------------------------------------------------------------- 登记

    @classmethod
    async def _register_product(
        cls,
        db: AsyncSession,
        *,
        resource_kind: str,
        server_id: str,
        agent_hasn_id: str | None,
        owner_id: str,
        session_id: str | None,
        project_id: str | None,
        title: str,
        summary: str | None,
        source_tool: str | None,
    ) -> None:
        """strict 登记（同事务，失败外抛）。主人手建（agent_hasn_id 为空）跳过登记，业务行照常写。"""
        if not agent_hasn_id:
            # register-on-write 判据「分身参与」：主人纯手工建、分身没碰过的不登记（05 §1.2）。
            return
        descriptor = ai_native_app_registry.resource_descriptor(_APP_ID, resource_kind)
        if descriptor is None:
            # descriptor 缺失是编码错误（manifest 未声明该 resource_kind）——严格登记必须外抛，
            # 不能像 best-effort 那样吞掉，否则产物永远登记不进 hasn_artifacts。
            raise errors.ServerError(msg=f'finance descriptor 缺失：{resource_kind}（manifest resources[] 未声明）')
        await hasn_artifacts_service.record_app_resource_artifact(
            db,
            descriptor=descriptor,
            server_id=server_id,
            session_id=session_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_id,
            title=title or resource_kind,
            summary=summary,
            source_tool=source_tool,
            project_id=project_id,
        )

    @classmethod
    async def _unregister_product(
        cls,
        db: AsyncSession,
        *,
        owner_id: str,
        resource_kind: str,
        server_id: str,
    ) -> None:
        """delete：strict 软删该资源的**全部** active 登记指针（按 owner + resource_uri）。"""
        descriptor = ai_native_app_registry.resource_descriptor(_APP_ID, resource_kind)
        if descriptor is None:
            raise errors.ServerError(msg=f'finance descriptor 缺失：{resource_kind}（manifest resources[] 未声明）')
        resource_uri = descriptor.build_uri(server_id)
        await hasn_artifacts_service.soft_delete_by_resource_uri(
            db, owner_hasn_id=owner_id, resource_uri=resource_uri
        )

    # ---------------------------------------------------------------- watchlist（非产物·不登记）

    @classmethod
    async def sync_watchlist(
        cls,
        db: AsyncSession,
        *,
        model_cls: Any,
        owner_id: str,
        op: str,
        op_id: str,
        base_revision: int | None,
        local_ref: str | None,
        server_id: str | None,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """watchlist `:sync`：只做版本闸/UPSERT/tombstone，**不登记 hasn_artifacts**（05 §3.1.1）。

        自选股是人工资产，无 agent_hasn_id/node_id 溯源列——沿用产物的幂等/乐观锁核心，
        只是不走登记分支。
        """
        if op == 'create':
            if not local_ref:
                raise errors.RequestError(msg='create 必须携带 local_ref')
            existing = (
                await db.execute(
                    select(model_cls).where(model_cls.owner_id == owner_id, model_cls.local_ref == local_ref).limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return {'id': str(existing.id), 'revision': int(existing.revision), 'op': op}
            row = model_cls(owner_id=owner_id, revision=1, last_client_op_id=op_id, status='active', **fields)
            db.add(row)
            await db.flush()
            return {'id': str(row.id), 'revision': int(row.revision), 'op': op}

        row, _ = await cls._locate_and_apply(
            db,
            model_cls=model_cls,
            owner_id=owner_id,
            op=op,
            op_id=op_id,
            base_revision=base_revision,
            server_id=server_id,
            fields=fields,
        )
        return {'id': str(row.id), 'revision': int(row.revision), 'op': op}


finance_sync_service = FinanceSyncService()
