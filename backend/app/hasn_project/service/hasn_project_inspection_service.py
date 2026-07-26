"""平台项目巡检建议的项目域业务服务（doc38 C11）。

本文件由 SQL codegen 生成后补充项目域规则。生成的泛型 CRUD 面以 bigint 主键和 user_id 为前提，
不适用于项目域的 UUID、owner_hasn_id 与 Agent JWT 身份模型，因此只保留生成的模型/CRUD 作为
表基线；所有可达读写从这里按项目 owner 过滤并通过父项目校验。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn_plan.model.todo import Todo
from backend.app.hasn_project.model import HasnProjectInspection
from backend.app.hasn_project.service.project_app_service import _as_uuid, _err, project_service, serialize
from backend.common.exception import errors
from backend.utils.timezone import timezone

_STATUSES = frozenset({'unread', 'dispatched', 'dismissed', 'reminded'})


def _required_text(value: Any, *, code: str, field_name: str, limit: int) -> str:
    """校验巡检工具的必填文本，拒绝把空值或过长内容写进权威记录。"""
    if not isinstance(value, str):
        raise _err(code, f'{field_name}不能为空')
    normalized = value.strip()
    if not normalized:
        raise _err(code, f'{field_name}不能为空')
    if len(normalized) > limit:
        raise _err(code, f'{field_name}长度不能超过 {limit} 个字符')
    return normalized


def _optional_text(value: Any, *, code: str, field_name: str) -> str | None:
    """规范可选建议指令；空白归一为空，非文本如实拒绝。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _err(code, f'{field_name}必须是字符串')
    normalized = value.strip()
    return normalized or None


class ProjectInspectionService:
    """项目巡检建议的发布、Owner 读取及处理状态机。"""

    async def publish(
        self,
        db: AsyncSession,
        *,
        owner: str,
        agent_id: str,
        project_id: str | UUID,
        fingerprint: Any,
        suggestion: Any,
        suggested_instruction: Any = None,
    ) -> dict[str, Any]:
        """由当前 Agent 发布建议，并以 owner/project/fingerprint 作幂等重放键。"""
        project = await project_service.resolve_active_project_for_work(db, owner=owner, pk=project_id)
        await project_service.assert_active_owned_agent(db, owner=owner, agent_id=agent_id)
        normalized_fingerprint = _required_text(
            fingerprint, code='INVALID_INSPECTION_FINGERPRINT', field_name='建议指纹', limit=128
        )
        normalized_suggestion = _required_text(
            suggestion, code='INVALID_INSPECTION_SUGGESTION', field_name='巡检建议', limit=20_000
        )
        normalized_instruction = _optional_text(
            suggested_instruction,
            code='INVALID_SUGGESTED_INSTRUCTION',
            field_name='建议派发指令',
        )
        now = timezone.now()
        stmt = insert(HasnProjectInspection).values(
            owner_id=owner,
            project_id=project.id,
            agent_id=agent_id,
            fingerprint=normalized_fingerprint,
            suggestion=normalized_suggestion,
            suggested_instruction=normalized_instruction,
            status='unread',
            inspected_time=now,
        )
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=['owner_id', 'project_id', 'fingerprint'],
            # 重放可修正文案和巡检时间，但绝不把主人已经处理的建议重置回 unread。
            set_={
                'agent_id': agent_id,
                'suggestion': normalized_suggestion,
                'suggested_instruction': normalized_instruction,
                'inspected_time': now,
                'updated_time': now,
            },
        )
        inspection_id = (await db.execute(upsert_stmt.returning(HasnProjectInspection.id))).scalar_one()
        row = await self._get_owned(db, owner=owner, project_id=project.id, inspection_id=inspection_id)
        return serialize(row)

    async def list_for_project(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        status: str | None = 'unread',
    ) -> list[dict[str, Any]]:
        """按 owner 项目读取建议；默认仅返回待处理建议，避免已处理卡片长期占据总览。"""
        project = await project_service.get_owned_project(db, owner=owner, pk=project_id)
        if status is not None and status not in _STATUSES:
            raise _err('INVALID_INSPECTION_STATUS', '巡检建议状态非法')
        stmt = sa.select(HasnProjectInspection).where(
            HasnProjectInspection.owner_id == owner,
            HasnProjectInspection.project_id == project.id,
        )
        if status is not None:
            stmt = stmt.where(HasnProjectInspection.status == status)
        rows = (await db.execute(stmt.order_by(HasnProjectInspection.inspected_time.desc()))).scalars().all()
        return [serialize(row) for row in rows]

    async def dismiss(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        inspection_id: str | UUID,
    ) -> dict[str, Any]:
        """主人明确忽略未读建议；重复忽略同一条是幂等成功。"""
        row = await self._get_owned(db, owner=owner, project_id=project_id, inspection_id=inspection_id)
        self._ensure_transition(row, target='dismissed')
        if row.status != 'dismissed':
            row.status = 'dismissed'
            row.handled_time = timezone.now()
            await db.flush()
        return serialize(row)

    async def mark_dispatched(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        inspection_id: str | UUID,
        work_session_id: Any,
    ) -> dict[str, Any]:
        """回填按建议派发真实创建的工作会话，禁止把其它主人或其它项目会话挂进来。"""
        row = await self._get_owned(db, owner=owner, project_id=project_id, inspection_id=inspection_id)
        normalized_session_id = _required_text(
            work_session_id, code='INVALID_WORK_SESSION_ID', field_name='工作会话 ID', limit=64
        )
        session = (
            await db.execute(
                sa.select(HasnSessions.session_id).where(
                    HasnSessions.session_id == normalized_session_id,
                    HasnSessions.owner_id == owner,
                    HasnSessions.project_id == row.project_id,
                )
            )
        ).scalar_one_or_none()
        if session is None:
            raise _err('INVALID_WORK_SESSION_ID', '工作会话不存在、不属于当前主人或未挂靠本项目', http_code=422)
        self._ensure_transition(row, target='dispatched', reference=normalized_session_id)
        if row.status != 'dispatched':
            row.status = 'dispatched'
            row.work_session_id = normalized_session_id
            row.handled_time = timezone.now()
            await db.flush()
        return serialize(row)

    async def mark_reminded(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        inspection_id: str | UUID,
        plan_todo_id: Any,
    ) -> dict[str, Any]:
        """回填提醒今晚创建的真实计划待办，拒绝无主人归属的任意整数引用。"""
        row = await self._get_owned(db, owner=owner, project_id=project_id, inspection_id=inspection_id)
        if not isinstance(plan_todo_id, int) or isinstance(plan_todo_id, bool) or plan_todo_id <= 0:
            raise _err('INVALID_PLAN_TODO_ID', '计划待办 ID 必须是正整数')
        todo = (
            await db.execute(
                sa.select(Todo.id).where(Todo.id == plan_todo_id, Todo.owner_hasn_id == owner)
            )
        ).scalar_one_or_none()
        if todo is None:
            raise _err('INVALID_PLAN_TODO_ID', '计划待办不存在或不属于当前主人', http_code=422)
        self._ensure_transition(row, target='reminded', reference=str(plan_todo_id))
        if row.status != 'reminded':
            row.status = 'reminded'
            row.plan_todo_id = plan_todo_id
            row.handled_time = timezone.now()
            await db.flush()
        return serialize(row)

    async def _get_owned(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        inspection_id: str | UUID,
    ) -> HasnProjectInspection:
        """先按 owner 校验父项目，再按 owner/project/id 读建议，避免跨主人泄漏。"""
        project = await project_service.get_owned_project(db, owner=owner, pk=project_id)
        row = (
            await db.execute(
                sa.select(HasnProjectInspection).where(
                    HasnProjectInspection.id == _as_uuid(inspection_id),
                    HasnProjectInspection.owner_id == owner,
                    HasnProjectInspection.project_id == project.id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='巡检建议不存在')
        return row

    @staticmethod
    def _ensure_transition(row: HasnProjectInspection, *, target: str, reference: str | None = None) -> None:
        """仅 unread 可进入处理态；同一终态且引用一致时保持幂等。"""
        if row.status == target:
            if target == 'dispatched' and row.work_session_id != reference:
                raise _err('INSPECTION_ALREADY_HANDLED', '该建议已派发到另一工作会话', http_code=409)
            if target == 'reminded' and str(row.plan_todo_id) != reference:
                raise _err('INSPECTION_ALREADY_HANDLED', '该建议已关联另一计划待办', http_code=409)
            return
        if row.status != 'unread':
            raise _err('INSPECTION_ALREADY_HANDLED', '该巡检建议已经处理，不能再次变更状态', http_code=409)


inspection_service = ProjectInspectionService()
# 兼容 codegen 的 service 导入名；业务调用应使用更明确的 ``inspection_service``。
hasn_project_inspection_service = inspection_service
