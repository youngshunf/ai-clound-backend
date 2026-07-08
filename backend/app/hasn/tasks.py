"""HASN workbench background tasks."""

from datetime import datetime, timedelta

from backend.app.task.celery import celery_app


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

        count = result.rowcount
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
    from backend.database.db import async_db_session

    async with async_db_session.begin() as session:
        count = await hasn_group_service.sweep_expired_invites(session)
    return f'expired {count} overdue group agent invites' if count else 'no overdue group agent invites'
