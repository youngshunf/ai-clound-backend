"""平台项目周报的产物化写入与读取服务（doc38 C13）。

周报不是一张平行业务表：正文直接复用统一 ``hasn_artifacts`` 的 ``document`` 产物与不可变
贡献记录。这样项目产物流、工作会话资源栏和 ``hasn://artifact/{id}`` 深链始终指向同一权威对象。
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_sessions import HasnSessions
from backend.app.hasn.schema.artifact_contract import ArtifactMutation
from backend.app.hasn.service.artifact_registration_service import artifact_registration_service
from backend.app.hasn_project.service.project_app_service import _err, project_service

_SOURCE_TOOL = 'hasn.project.report.publish'
_SOURCE_APP_ID = 'project'
_REPORT_PREFIX = 'project-report'


def _required_text(value: Any, *, code: str, field_name: str, limit: int) -> str:
    """规范周报必填文本，避免空白或过长正文进入权威产物。"""
    if not isinstance(value, str):
        raise _err(code, f'{field_name}不能为空')
    normalized = value.strip()
    if not normalized:
        raise _err(code, f'{field_name}不能为空')
    if len(normalized) > limit:
        raise _err(code, f'{field_name}长度不能超过 {limit} 个字符')
    return normalized


def _optional_text(value: Any, *, code: str, field_name: str, limit: int) -> str | None:
    """规范可选摘要；空白不伪造摘要，保留为 null。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _err(code, f'{field_name}必须是字符串')
    normalized = value.strip()
    if len(normalized) > limit:
        raise _err(code, f'{field_name}长度不能超过 {limit} 个字符')
    return normalized or None


def _period(value: Any, *, field_name: str) -> date:
    """把工具的 ISO 日期周期字段解析为 ``date``，非法值显式拒绝。"""
    if not isinstance(value, str):
        raise _err('INVALID_REPORT_PERIOD', f'{field_name}必须是 YYYY-MM-DD 日期')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _err('INVALID_REPORT_PERIOD', f'{field_name}必须是 YYYY-MM-DD 日期') from exc


class ProjectReportService:
    """项目周报的统一 document 产物写点与稳定索引读模型。"""

    async def publish(
        self,
        db: AsyncSession,
        *,
        owner: str,
        agent_id: str,
        project_id: str | UUID,
        work_session_id: Any,
        period_start: Any,
        period_end: Any,
        title: Any,
        body: Any,
        summary: Any = None,
    ) -> dict[str, Any]:
        """把分身生成的周报登记为项目 ``document`` 产物；同周期同内容重放幂等。"""
        project = await project_service.resolve_active_project_for_work(db, owner=owner, pk=project_id)
        await project_service.assert_active_owned_agent(db, owner=owner, agent_id=agent_id)
        session_id = _required_text(
            work_session_id,
            code='INVALID_WORK_SESSION_ID',
            field_name='生成周报的工作会话 ID',
            limit=64,
        )
        session = (
            await db.execute(
                sa.select(HasnSessions.session_id).where(
                    HasnSessions.session_id == session_id,
                    HasnSessions.owner_id == owner,
                    HasnSessions.hasn_id == agent_id,
                    HasnSessions.project_id == project.id,
                )
            )
        ).scalar_one_or_none()
        if session is None:
            raise _err(
                'INVALID_WORK_SESSION_ID',
                '工作会话不存在、不属于当前分身或未挂靠本项目',
                http_code=422,
            )

        start = _period(period_start, field_name='period_start')
        end = _period(period_end, field_name='period_end')
        if start > end:
            raise _err('INVALID_REPORT_PERIOD', 'period_start 不能晚于 period_end')
        normalized_title = _required_text(title, code='INVALID_REPORT_TITLE', field_name='周报标题', limit=256)
        normalized_body = _required_text(body, code='INVALID_REPORT_BODY', field_name='周报正文', limit=100_000)
        normalized_summary = _optional_text(
            summary,
            code='INVALID_REPORT_SUMMARY',
            field_name='周报摘要',
            limit=2_000,
        )

        # 一个项目在一个周期只有一个权威周报对象。正文/标题摘要组成内容指纹，完全相同的重放不重复贡献；
        # 内容变更会更新同一 artifact，并以 update 参与记录保留本次真实再生成。
        report_key = f'{_REPORT_PREFIX}:{project.id}:{start.isoformat()}:{end.isoformat()}'
        artifact_key = f'body:{_SOURCE_APP_ID}:{report_key}'
        existing_artifact_id = (
            await db.execute(
                sa.select(HasnArtifacts.artifact_id).where(
                    HasnArtifacts.owner_hasn_id == owner,
                    HasnArtifacts.artifact_key == artifact_key,
                )
            )
        ).scalar_one_or_none()
        content_fingerprint = sha256(
            f'{normalized_title}\x00{normalized_summary or ""}\x00{normalized_body}'.encode('utf-8')
        ).hexdigest()
        mutation = ArtifactMutation(
            owner_hasn_id=owner,
            agent_hasn_id=agent_id,
            action='update' if existing_artifact_id else 'create',
            source_kind='platform_tool',
            artifact_kind='document',
            body=normalized_body,
            project_id=str(project.id),
            work_session_id=session_id,
            source_tool=_SOURCE_TOOL,
            source_app_id=_SOURCE_APP_ID,
            dispatch_id=session_id,
            source_event_id=report_key,
            idempotency_key=f'{report_key}:{content_fingerprint}',
            title=normalized_title,
            summary=normalized_summary,
            metadata={
                'report_kind': 'weekly',
                'period_start': start.isoformat(),
                'period_end': end.isoformat(),
                'work_session_id': session_id,
                'report_key': report_key,
            },
        )
        registration = await artifact_registration_service.register(db, mutation)
        return {
            'artifact_id': registration.artifact_id,
            'uri': f'hasn://artifact/{registration.artifact_id}',
            'project_id': str(project.id),
            'period_start': start.isoformat(),
            'period_end': end.isoformat(),
        }

    async def list_for_project(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列该项目真实周报索引；正文仍在产物详情，不复制成第二套文档存储。"""
        project = await project_service.get_owned_project(db, owner=owner, pk=project_id)
        rows = (
            await db.execute(
                sa.select(HasnArtifacts)
                .where(
                    HasnArtifacts.owner_hasn_id == owner,
                    HasnArtifacts.project_id == project.id,
                    HasnArtifacts.artifact_kind == 'document',
                    # `source_tool` 属于不可变 contribution 上下文，不是 artifact 当前态的稳定列；
                    # 周报索引以本写点保存的 report_kind 元数据识别，避免把任意项目文档混进周报卡。
                    HasnArtifacts.meta_data['report_kind'].astext == 'weekly',
                    HasnArtifacts.status == 'active',
                )
                .order_by(HasnArtifacts.updated_time.desc(), HasnArtifacts.created_time.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [self._serialize_report(row) for row in rows]

    @staticmethod
    def _serialize_report(row: HasnArtifacts) -> dict[str, Any]:
        """把统一产物收敛成项目周报卡索引，URI 只使用云端权威 artifact_id。"""
        metadata = row.meta_data if isinstance(row.meta_data, dict) else {}
        period_start = metadata.get('period_start')
        period_end = metadata.get('period_end')
        period = f'{period_start} ~ {period_end}' if period_start and period_end else None
        return {
            'report_id': row.artifact_id,
            'title': row.title,
            'summary': row.summary,
            'meta': f'本周 · {period}' if period else '项目周报',
            'period_start': period_start,
            'period_end': period_end,
            'resource_uri': f'hasn://artifact/{row.artifact_id}',
            'created_time': row.created_time.isoformat() if row.created_time else None,
            'updated_time': row.updated_time.isoformat() if row.updated_time else None,
            'work_session_id': metadata.get('work_session_id'),
        }


report_service = ProjectReportService()
