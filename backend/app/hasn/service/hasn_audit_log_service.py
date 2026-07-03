from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_audit_log import hasn_audit_log_dao
from backend.app.hasn.model import HasnAuditLog
from backend.app.hasn.schema.hasn_audit_log import (
    CreateHasnAuditLogParam,
    DeleteHasnAuditLogParam,
    UpdateHasnAuditLogParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAuditLogService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAuditLog:
        """
        获取HASN 审计日志

        :param db: 数据库会话
        :param pk: HASN 审计日志 ID
        :return:
        """
        hasn_audit_log = await hasn_audit_log_dao.get(db, pk)
        if not hasn_audit_log:
            raise errors.NotFoundError(msg='HASN 审计日志不存在')
        return hasn_audit_log

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 审计日志列表

        :param db: 数据库会话
        :return:
        """
        hasn_audit_log_select = await hasn_audit_log_dao.get_select()
        return await paging_data(db, hasn_audit_log_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAuditLog]:
        """
        获取所有HASN 审计日志

        :param db: 数据库会话
        :return:
        """
        hasn_audit_logs = await hasn_audit_log_dao.get_all(db)
        return hasn_audit_logs

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAuditLogParam) -> None:
        """
        创建HASN 审计日志

        :param db: 数据库会话
        :param obj: 创建HASN 审计日志参数
        :return:
        """
        await hasn_audit_log_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAuditLogParam) -> int:
        """
        更新HASN 审计日志

        :param db: 数据库会话
        :param pk: HASN 审计日志 ID
        :param obj: 更新HASN 审计日志参数
        :return:
        """
        count = await hasn_audit_log_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAuditLogParam) -> int:
        """
        删除HASN 审计日志

        :param db: 数据库会话
        :param obj: HASN 审计日志 ID 列表
        :return:
        """
        count = await hasn_audit_log_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def append(
        *,
        db: AsyncSession,
        actor_id: str,
        actor_type: str = 'system',
        action: str,
        target_id: str | None = None,
        target_type: str | None = None,
        details: dict,
        severity: str | None = None,
    ) -> HasnAuditLog:
        """Phase 7 因果链追加 (设计 05 §3.2)

        在单 flush 内 SELECT 同 actor_id 最新行 → SHA-256(prev_hash + canonical_json(details))
        → INSERT 新行。actor_id 作链作用域 (每个 actor_id 一条独立因果链)。

        :param db: AsyncSession (调用方负责 commit)
        :param actor_id: 链作用域键
        :param actor_type: human / agent / system
        :param action: 事件类型 (permission_decision / spawner_dispatch / ...)
        :param target_id: 目标 hasn_id (可选)
        :param target_type: human / agent / message / ...
        :param details: 事件详情 (canonical_json 后用于 hash)
        :param severity: info / warning / error (默认 None)
        :return: 新写入的 HasnAuditLog 实例 (含 id 与 hash_chain)
        """
        import hashlib
        import json

        from sqlalchemy import select

        from backend.app.hasn.model.hasn_audit_log import HasnAuditLog

        prev = (
            await db.execute(
                select(HasnAuditLog)
                .where(HasnAuditLog.actor_id == actor_id)
                .order_by(HasnAuditLog.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        prev_id = prev.id if prev else None
        prev_hash = prev.hash_chain if prev else ''
        payload_json = json.dumps(
            details, sort_keys=True, separators=(',', ':'), ensure_ascii=False
        )
        hash_chain = hashlib.sha256(
            (prev_hash + payload_json).encode('utf-8')
        ).hexdigest()

        entry = HasnAuditLog(
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            target_id=target_id,
            target_type=target_type,
            details=details,
            prev_log_id=prev_id,
            hash_chain=hash_chain,
            findings=[],
            severity=severity,
        )
        db.add(entry)
        await db.flush()
        return entry


hasn_audit_log_service: HasnAuditLogService = HasnAuditLogService()
