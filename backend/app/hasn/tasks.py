"""HASN workbench background tasks."""

from datetime import datetime, timedelta

from backend.app.task.celery import celery_app


@celery_app.task(name='hasn_relation_outbox_dispatch')
async def hasn_relation_outbox_dispatch() -> str:
    """投递身份事实到 IM 控制边的可靠关系命令。"""
    from backend.app.hasn.service.hasn_relation_command_outbox_service import (
        RelationCommandOutboxRelay,
    )
    from backend.app.hasn_im.application.provider import get_relation_gateway
    from backend.database.db import python_backend_db_session

    relay = RelationCommandOutboxRelay(
        session_factory=python_backend_db_session,
        relation_gateway=get_relation_gateway(),
    )
    stats = await relay.drain_once()
    return (
        f'claimed={stats.claimed} completed={stats.completed} '
        f'retried={stats.retried} dead_lettered={stats.dead_lettered}'
    )


@celery_app.task(name='hasn_check_agent_heartbeat_timeout', bind=True)
async def hasn_check_agent_heartbeat_timeout(self) -> str:
    """检查 agent 心跳超时，将超过 1 小时未上报的 agent 标记为离线。

    定时执行：每 5 分钟一次
    超时阈值：1 小时
    """
    import sqlalchemy as sa

    from backend.app.hasn.model import HasnAgents
    from backend.database.db import async_db_session

    timeout_threshold = datetime.utcnow() - timedelta(hours=1)

    async with async_db_session() as session:
        # 查找超时的 agent：在线状态为 online 且最后心跳时间超过 1 小时
        result = await session.execute(
            sa.update(HasnAgents)
            .where(
                HasnAgents.online_status == 'online',
                HasnAgents.last_heartbeat_at < timeout_threshold,
            )
            .values(
                online_status='offline',
                binding_status='unbound',
                binding_node_id=None,
            )
        )
        await session.commit()

        count = getattr(result, 'rowcount', 0) or 0
        if count > 0:
            return f'marked {count} agents as offline due to heartbeat timeout'
        return 'no agents timed out'


@celery_app.task(name='app_entitlement_expire_sweep')
async def app_entitlement_expire_sweep() -> str:
    """把 ``expires_at`` 已过的 active 应用权益置 expired（设计 §5.4 定时兜底）。

    定时执行：每天凌晨 2 点（订阅过期检查之后）。
    读路径本就按 ``expires_at`` 过滤（``get_active_entitlement``），本任务只收敛存量 status，
    让「active 但已过期」的行不长期占着 ``uq_app_entitlement_active`` partial unique
    （到期复购的即时让位已由 ``grant_entitlement`` 内联处理，见其 docstring）。
    """
    from backend.app.hasn.service.app_catalog_service import sweep_expired_entitlements
    from backend.database.db import async_db_session

    async with async_db_session.begin() as session:
        count = await sweep_expired_entitlements(session)
    return f'expired {count} overdue app entitlements' if count else 'no overdue app entitlements'


@celery_app.task(name='hasn_group_agent_invite_expire_sweep')
async def hasn_group_agent_invite_expire_sweep() -> str:
    """把超 7 天未处理的拉分身邀请（doc10 §3.2）置 expired（定时兜底）。

    读路径已惰性判定过期（群详情/accept 时），本任务只收敛存量 pending 状态，
    让「pending 但已过期」的行不长期占着 ``uq_hasn_group_agent_invites_pending``
    partial unique（其主人下次可被重新邀请）。建议每天凌晨执行一次。
    """
    from backend.app.hasn.service.hasn_group_service import hasn_group_service
    from backend.database.db import im_service_db_session

    async with im_service_db_session.begin() as session:
        count = await hasn_group_service.sweep_expired_invites(session)
    return f'expired {count} overdue group agent invites' if count else 'no overdue group agent invites'


@celery_app.task(name='hasn_node_binding_expire_sweep')
async def hasn_node_binding_expire_sweep() -> str:
    """把已过期（expires_at <= now）仍标 active 的 Owner Binding 租约置 expired（定时兜底）。

    定时执行：每天凌晨 2:15。
    鉴权/路由热路径本就按 ``expires_at > now`` 过滤（``get_active_binding`` /
    ``list_active_bindings``），过期租约不参与路由，安全无虞；本任务只收敛存量 status，
    让设备管理页 / 审计反映真实租约状态——否则 ``hasn_node_bindings.status`` 长年
    全是 active（sweeper 从未接线的历史问题，2026-07-14 福仔发现）。
    """
    from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
    from backend.database.db import async_db_session

    async with async_db_session.begin() as session:
        count = await hasn_node_bindings_service.expire_stale_bindings(db=session)
    return f'expired {count} stale node owner bindings' if count else 'no stale node owner bindings'


@celery_app.task(name='hasn_contact_lifecycle_expire_sweep')
async def hasn_contact_lifecycle_expire_sweep() -> str:
    """关系生命周期过期兜底（doc08 RT5·B7）：好友请求 30 天未响应过期 + 联系人 auto_expire 到期。

    定时执行：每天凌晨 2:20。
    ① hasn_contact_requests：pending 且创建超 30 天 → expired（幂等，只收敛存量 pending）；
    ② hasn_contacts：auto_expire 已过且仍 connected → archived（service 到期自动断，铁律5b）。
    """
    from backend.app.hasn_im.application.provider import get_relation_gateway

    result = await get_relation_gateway().sweep_expired_relation_lifecycle()
    req_n = result['requests_expired']
    ct_n = result['contacts_expired']
    if req_n or ct_n:
        return f'expired {req_n} contact requests, archived {ct_n} auto-expire contacts'
    return 'no overdue contact requests or auto-expire contacts'


@celery_app.task(name='hasn_artifact_registration_reconcile')
async def hasn_artifact_registration_reconcile() -> str:
    """定期重放失败的产物登记意图，并补齐贡献记录缺失的 outbox。"""
    from sqlalchemy import select

    from backend.app.hasn.model import HasnArtifactRegistrationOutbox, HasnArtifacts
    from backend.app.hasn.service.artifact_registration_outbox_service import (
        artifact_registration_outbox_service,
    )
    from backend.database.db import async_db_session

    async with async_db_session.begin() as session:
        owners = list(
            (
                await session.execute(
                    select(HasnArtifacts.owner_hasn_id)
                    .union(
                        select(HasnArtifactRegistrationOutbox.owner_hasn_id).where(
                            HasnArtifactRegistrationOutbox.status == 'pending'
                        )
                    )
                    .limit(500)
                )
            ).scalars()
        )
        repaired = 0
        for owner_hasn_id in owners:
            repaired += await artifact_registration_outbox_service.reconcile(
                session,
                owner_hasn_id=owner_hasn_id,
            )

    return f'reconciled {repaired} artifact registration records for {len(owners)} owners'


@celery_app.task(name='owner_storage_job_dispatch')
async def owner_storage_job_dispatch() -> str:
    """执行用户云存储导出、迁移、对象回收与补偿 outbox。"""
    from backend.app.hasn.service.owner_storage_service import OwnerStorageService
    from backend.database.db import async_db_session

    processed = await OwnerStorageService(async_db_session).process_jobs(limit=100)
    return f'processed {processed} owner storage jobs'


@celery_app.task(name='owner_storage_upload_lifecycle_sweep')
async def owner_storage_upload_lifecycle_sweep() -> str:
    """清理过期 multipart 与上传预占，供应商确认终止后才释放额度。"""
    from backend.app.hasn.service.owner_storage_maintenance_service import (
        OwnerStorageMaintenanceService,
    )
    from backend.database.db import async_db_session

    maintenance = OwnerStorageMaintenanceService(async_db_session)
    multipart = await maintenance.sweep_expired_multipart(limit=500)
    reservations = await maintenance.sweep_expired_reservations(limit=1000)
    return (
        f'multipart_checked={multipart["checked"]} multipart_aborted={multipart["aborted"]} '
        f'multipart_failed={multipart["failed"]} '
        f'reservations_completed={reservations["completed"]} '
        f'reservations_expired={reservations["expired"]}'
    )


@celery_app.task(name='owner_storage_legacy_backfill')
async def owner_storage_legacy_backfill() -> str:
    """分批把旧资产位置投影为对象层，兼容窗口内由双读保证可访问。"""
    from backend.app.hasn.service.owner_storage_maintenance_service import (
        OwnerStorageMaintenanceService,
    )
    from backend.database.db import async_db_session

    result = await OwnerStorageMaintenanceService(async_db_session).backfill_legacy_assets(
        batch_size=500,
        verify_objects=False,
    )
    return (
        f'assets_backfilled={result["assets_backfilled"]} '
        f'objects_created={result["objects_created"]} '
        f'owners_without_identity={result["owners_without_identity"]}'
    )


@celery_app.task(name='owner_storage_retention_sweep')
async def owner_storage_retention_sweep() -> str:
    """执行导出过期、迁移观察期清源与保守的无引用资产回收。"""
    from backend.app.hasn.service.owner_storage_maintenance_service import (
        OwnerStorageMaintenanceService,
    )
    from backend.database.db import async_db_session

    maintenance = OwnerStorageMaintenanceService(async_db_session)
    exports = await maintenance.sweep_expired_exports(limit=500)
    migrations = await maintenance.sweep_migration_sources(limit=500)
    unbound = await maintenance.sweep_unbound_assets(limit=500)
    return (
        f'exports_checked={exports["checked"]} exports_purged={exports["purged"]} '
        f'migrations_checked={migrations["checked"]} migrations_deleted={migrations["deleted"]} '
        f'unbound_checked={unbound["checked"]} unbound_trashed={unbound["trashed"]}'
    )


@celery_app.task(name='owner_storage_reconcile')
async def owner_storage_reconcile() -> str:
    """按 Owner 以数据库为权威游标核对对象、引用计数和已用额度。"""
    from sqlalchemy import text

    from backend.app.hasn.service.owner_storage_maintenance_service import (
        OwnerStorageMaintenanceService,
    )
    from backend.database.db import async_db_session

    async with async_db_session() as db:
        owners = [
            str(owner)
            for owner in (
                await db.execute(
                    text(
                        """
                        SELECT owner_hasn_id
                        FROM hasn_storage_accounts
                        ORDER BY owner_hasn_id
                        """
                    )
                )
            ).scalars()
        ]
    maintenance = OwnerStorageMaintenanceService(async_db_session)
    repaired = 0
    for owner_hasn_id in owners:
        result = await maintenance.reconcile_owner(
            owner_hasn_id=owner_hasn_id,
            verify_objects=True,
            repair_counters=True,
        )
        repaired += int(result['ref_count_repairs'])
        if int(result['used_bytes_before']) != int(result['used_bytes_after']):
            repaired += 1
    return f'reconciled {len(owners)} owner storage accounts, repaired {repaired} differences'
