"""Growth 项目基础资源的可靠、可恢复 provisioning 编排。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy import event

from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.growth_project_provision import (
    GrowthProjectProvision,
)
from backend.app.hasn_knowledge.model.document import Document
from backend.app.hasn_knowledge.model.folder import Folder
from backend.app.hasn_knowledge.model.kb import Kb
from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service
from backend.app.hasn_knowledge.service.ragflow_client import (
    KnowledgeProviderError,
)
from backend.common.exception import errors
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


PROVISION_STEPS = (
    'create_funnel',
    'create_knowledge',
    'attach_knowledge',
    'seed_knowledge',
)
_STEP_LABELS = {
    'create_funnel': '创建获客漏斗',
    'create_knowledge': '创建项目知识库',
    'attach_knowledge': '挂靠项目知识库',
    'seed_knowledge': '创建基础资料',
}
_KNOWLEDGE_REQUEST_PREFIX = 'growth'
_STALE_RUNNING_AFTER = timedelta(minutes=15)
_MAX_AUTOMATIC_ATTEMPTS = 8
_SEED_FOLDER_NAME = '获客基础资料'
_SEED_DOCUMENTS = (
    (
        '产品与服务',
        '# 产品与服务\n\n'
        '此文档由获客开通流程创建，用于承载主人确认后的产品事实。\n\n'
        '## 当前状态\n\n尚未填写。请补充产品、服务范围、价格边界与可验证优势。\n',
    ),
    (
        '理想客户画像',
        '# 理想客户画像\n\n'
        '此文档用于保存主人确认后的目标客户条件，分身不得把未核实推断写成事实。\n\n'
        '## 当前状态\n\n尚未填写。请补充行业、规模、地区、关键角色与排除条件。\n',
    ),
    (
        '品牌与合规边界',
        '# 品牌与合规边界\n\n'
        '此文档用于保存主人明确授权的品牌表达、禁用承诺与触达合规要求。\n\n'
        '## 当前状态\n\n尚未填写。未确认前，分身不得代替主人作出承诺或扩大触达授权。\n',
    ),
)


def _knowledge_request_id(growth_project_id: UUID) -> str:
    return f'{_KNOWLEDGE_REQUEST_PREFIX}:{growth_project_id}:knowledge'


def enqueue_growth_provision_after_commit(
    db: AsyncSession,
    growth_project_id: str | UUID,
) -> None:
    """事务提交后入队；broker 失败保留 pending，由手动重试和 reconcile 接管。"""
    canonical_id = str(growth_project_id)

    def _enqueue(_sync_session: Any) -> None:
        try:
            from backend.app.hasn_growth.tasks import growth_project_provision

            growth_project_provision.delay(canonical_id)
            log.info(
                '[GrowthProvision] 开通命令已入队: growth_project_id=%s',
                canonical_id,
            )
        except Exception as exc:
            log.warning(
                '[GrowthProvision] 入队失败，已落库等待重试: growth_project_id=%s error_type=%s',
                canonical_id,
                exc.__class__.__name__,
            )

    event.listen(db.sync_session, 'after_commit', _enqueue, once=True)


def _safe_error(exc: Exception) -> dict[str, str]:
    if isinstance(exc, KnowledgeProviderError):
        return {'code': exc.code, 'message': exc.message}
    if isinstance(exc, errors.BaseExceptionError):
        return {
            'code': exc.__class__.__name__,
            'message': str(exc.msg),
        }
    return {
        'code': exc.__class__.__name__,
        'message': '基础资源开通失败，请查看服务日志后重试',
    }


class GrowthProjectProvisionService:
    """以数据库步骤事实驱动真实 Knowledge 资源创建。"""

    @staticmethod
    async def _locked_project(
        db: AsyncSession,
        growth_project_id: UUID,
    ) -> GrowthProject | None:
        return (
            await db.execute(sa.select(GrowthProject).where(GrowthProject.id == growth_project_id).with_for_update())
        ).scalar_one_or_none()

    @staticmethod
    async def _ordered_rows(
        db: AsyncSession,
        growth_project_id: UUID,
    ) -> list[GrowthProjectProvision]:
        rows = (
            await db.execute(
                sa
                .select(GrowthProjectProvision)
                .where(GrowthProjectProvision.growth_project_id == growth_project_id)
                .order_by(GrowthProjectProvision.id)
            )
        ).scalars()
        return list(rows)

    async def _claim_next(
        self,
        growth_project_id: UUID,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        async with async_db_session.begin() as db:
            growth = await self._locked_project(db, growth_project_id)
            if growth is None:
                return {'status': 'not_found'}
            if growth.status in {'paused', 'archived'}:
                return {'status': growth.status}
            rows = await self._ordered_rows(db, growth_project_id)
            if len(rows) != len(PROVISION_STEPS):
                growth.provision_status = 'failed'
                growth.provision_error = {
                    'code': 'PROVISION_STATE_INCOMPLETE',
                    'message': '开通步骤状态不完整，请运行修复',
                }
                return {'status': 'invalid_state'}

            next_row = next(
                (row for row in rows if row.status != 'success'),
                None,
            )
            if next_row is None:
                growth.provision_status = 'ready'
                growth.provision_error = None
                if growth.status == 'draft':
                    growth.status = 'active'
                return {'status': 'ready'}

            if next_row.status == 'running':
                stale_before = now - _STALE_RUNNING_AFTER
                updated_time = next_row.updated_time or next_row.started_time
                if updated_time is None or updated_time > stale_before:
                    return {'status': 'busy', 'step': next_row.step}
            if next_row.status == 'failed' and next_row.next_retry_time is not None and next_row.next_retry_time > now:
                return {
                    'status': 'waiting_retry',
                    'step': next_row.step,
                    'next_retry_time': next_row.next_retry_time.isoformat(),
                }
            if next_row.status == 'failed' and next_row.attempts >= _MAX_AUTOMATIC_ATTEMPTS:
                return {'status': 'exhausted', 'step': next_row.step}

            next_row.status = 'running'
            next_row.attempts += 1
            next_row.started_time = now
            next_row.finished_time = None
            next_row.last_error = None
            next_row.next_retry_time = None
            growth.provision_status = 'running'
            growth.provision_error = None
            return {
                'status': 'claimed',
                'step': next_row.step,
                'attempts': next_row.attempts,
            }

    @staticmethod
    async def _load_growth(
        db: AsyncSession,
        growth_project_id: UUID,
    ) -> GrowthProject:
        growth = await db.get(GrowthProject, growth_project_id)
        if growth is None:
            raise errors.NotFoundError(msg='获客项目不存在')
        return growth

    async def _create_knowledge(self, growth_project_id: UUID) -> None:
        async with async_db_session.begin() as db:
            growth = await self._load_growth(db, growth_project_id)
            await knowledge_service.create_kb(
                db,
                growth.owner_hasn_id,
                name=f'{growth.name} · 获客知识库',
                description='与获客漏斗自动挂靠的项目知识库',
                platform_project_id=str(growth.platform_project_id),
                client_request_id=_knowledge_request_id(growth.id),
            )

    async def _attach_knowledge(self, growth_project_id: UUID) -> None:
        async with async_db_session.begin() as db:
            growth = await self._load_growth(db, growth_project_id)
            kb = (
                await db.execute(
                    sa.select(Kb).where(
                        Kb.owner_id == growth.owner_hasn_id,
                        Kb.client_request_id == _knowledge_request_id(growth.id),
                        Kb.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if kb is None:
                raise errors.ConflictError(
                    msg='项目知识库尚未创建，不能执行挂靠',
                    data={'error_code': 'GROWTH_KNOWLEDGE_MISSING'},
                )
            if kb.platform_project_id != growth.platform_project_id:
                raise errors.ConflictError(
                    msg='项目知识库挂靠到了其他平台项目',
                    data={'error_code': 'GROWTH_KNOWLEDGE_PROJECT_MISMATCH'},
                )
            growth.kb_ref = f'hasn://knowledge/kbs/{kb.id}'
            await db.flush()

    async def _knowledge_for_growth(
        self,
        db: AsyncSession,
        growth_project_id: UUID,
    ) -> tuple[GrowthProject, Kb]:
        growth = await self._load_growth(db, growth_project_id)
        kb = (
            await db.execute(
                sa.select(Kb).where(
                    Kb.owner_id == growth.owner_hasn_id,
                    Kb.client_request_id == _knowledge_request_id(growth.id),
                    Kb.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        if kb is None:
            raise errors.ConflictError(
                msg='项目知识库尚未创建，不能初始化基础资料',
                data={'error_code': 'GROWTH_KNOWLEDGE_MISSING'},
            )
        return growth, kb

    async def _ensure_seed_folder(
        self,
        growth_project_id: UUID,
    ) -> int:
        async with async_db_session.begin() as db:
            growth, kb = await self._knowledge_for_growth(
                db,
                growth_project_id,
            )
            existing = (
                await db.execute(
                    sa.select(Folder).where(
                        Folder.kb_id == kb.id,
                        Folder.owner_id == growth.owner_hasn_id,
                        Folder.parent_id.is_(None),
                        Folder.name == _SEED_FOLDER_NAME,
                        Folder.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing.id
            created = await knowledge_service.create_folder(
                db,
                growth.owner_hasn_id,
                kb.id,
                name=_SEED_FOLDER_NAME,
            )
            return int(created['id'])

    async def _ensure_seed_document(
        self,
        growth_project_id: UUID,
        *,
        folder_id: int,
        title: str,
        content: str,
    ) -> None:
        async with async_db_session.begin() as db:
            growth, kb = await self._knowledge_for_growth(
                db,
                growth_project_id,
            )
            existing = (
                await db.execute(
                    sa.select(Document.id).where(
                        Document.kb_id == kb.id,
                        Document.owner_id == growth.owner_hasn_id,
                        Document.folder_id == folder_id,
                        Document.kind == 'native',
                        Document.name == title,
                        Document.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            await knowledge_service.create_native_document(
                db,
                growth.owner_hasn_id,
                kb.id,
                title=title,
                content=content,
                folder_id=folder_id,
                source='system',
            )

    async def _seed_knowledge(self, growth_project_id: UUID) -> None:
        folder_id = await self._ensure_seed_folder(growth_project_id)
        for title, content in _SEED_DOCUMENTS:
            await self._ensure_seed_document(
                growth_project_id,
                folder_id=folder_id,
                title=title,
                content=content,
            )

    async def _execute_step(
        self,
        growth_project_id: UUID,
        step: str,
    ) -> None:
        if step == 'create_knowledge':
            await self._create_knowledge(growth_project_id)
            return
        if step == 'attach_knowledge':
            await self._attach_knowledge(growth_project_id)
            return
        if step == 'seed_knowledge':
            await self._seed_knowledge(growth_project_id)
            return
        if step == 'create_funnel':
            return
        raise RuntimeError(f'未知 provisioning 步骤：{step}')

    async def _mark_success(
        self,
        growth_project_id: UUID,
        step: str,
    ) -> None:
        async with async_db_session.begin() as db:
            growth = await self._locked_project(db, growth_project_id)
            if growth is None:
                return
            row = (
                await db.execute(
                    sa
                    .select(GrowthProjectProvision)
                    .where(
                        GrowthProjectProvision.growth_project_id == growth_project_id,
                        GrowthProjectProvision.step == step,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            row.status = 'success'
            row.finished_time = datetime.now(UTC)
            row.last_error = None
            row.next_retry_time = None
            rows = await self._ordered_rows(db, growth_project_id)
            if all(item.status == 'success' for item in rows):
                growth.provision_status = 'ready'
                growth.provision_error = None
                if growth.status == 'draft':
                    growth.status = 'active'

    async def _mark_failure(
        self,
        growth_project_id: UUID,
        step: str,
        attempts: int,
        exc: Exception,
    ) -> dict[str, Any]:
        error = _safe_error(exc)
        retry_seconds = min(30 * (2 ** max(attempts - 1, 0)), 3600)
        retry_time = datetime.now(UTC) + timedelta(seconds=retry_seconds)
        async with async_db_session.begin() as db:
            growth = await self._locked_project(db, growth_project_id)
            if growth is None:
                return {'status': 'not_found'}
            row = (
                await db.execute(
                    sa
                    .select(GrowthProjectProvision)
                    .where(
                        GrowthProjectProvision.growth_project_id == growth_project_id,
                        GrowthProjectProvision.step == step,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            row.status = 'failed'
            row.finished_time = datetime.now(UTC)
            row.last_error = error
            row.next_retry_time = retry_time
            growth.provision_status = 'failed'
            growth.provision_error = {
                **error,
                'step': step,
                'step_label': _STEP_LABELS[step],
            }
        return {
            'status': 'failed',
            'step': step,
            'attempts': attempts,
            'retry_in_seconds': (retry_seconds if attempts < _MAX_AUTOMATIC_ATTEMPTS else None),
            'error': error,
        }

    async def run(self, growth_project_id: str | UUID) -> dict[str, Any]:
        """从首个未成功步骤继续；每个外部写点独立提交，失败不抹掉已成功资源。"""
        canonical_id = UUID(str(growth_project_id))
        while True:
            claim = await self._claim_next(canonical_id)
            if claim['status'] != 'claimed':
                return claim
            step = str(claim['step'])
            attempts = int(claim['attempts'])
            try:
                await self._execute_step(canonical_id, step)
            except Exception as exc:
                log.warning(
                    '[GrowthProvision] 步骤失败: growth_project_id=%s step=%s error_type=%s',
                    canonical_id,
                    step,
                    exc.__class__.__name__,
                )
                return await self._mark_failure(
                    canonical_id,
                    step,
                    attempts,
                    exc,
                )
            await self._mark_success(canonical_id, step)

    async def retry(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        growth_project_id: str | UUID,
    ) -> GrowthProject:
        """主人显式重试首个失败/滞留步骤；不创建第二组步骤或外部资源。"""
        canonical_id = UUID(str(growth_project_id))
        growth = (
            await db.execute(
                sa
                .select(GrowthProject)
                .where(
                    GrowthProject.id == canonical_id,
                    GrowthProject.owner_hasn_id == owner_hasn_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if growth is None:
            raise errors.NotFoundError(msg='平台项目或获客漏斗不存在')
        if growth.status in {'paused', 'archived'}:
            raise errors.ConflictError(
                msg='暂停或归档状态不能重试开通，请先显式恢复',
                data={'error_code': 'GROWTH_PROJECT_INACTIVE'},
            )
        rows = await self._ordered_rows(db, canonical_id)
        target = next(
            (row for row in rows if row.status != 'success'),
            None,
        )
        if target is None:
            growth.provision_status = 'ready'
            growth.provision_error = None
            return growth
        target.status = 'pending'
        target.next_retry_time = None
        target.last_error = None
        target.finished_time = None
        growth.provision_status = 'pending'
        growth.provision_error = None
        await db.flush()
        return growth

    async def due_for_reconcile(self) -> list[str]:
        """认领到期失败或超时 running 项目，供 beat 重新投递。"""
        now = datetime.now(UTC)
        stale_before = now - _STALE_RUNNING_AFTER
        async with async_db_session.begin() as db:
            ids = (
                await db.execute(
                    sa
                    .select(GrowthProjectProvision.growth_project_id)
                    .join(
                        GrowthProject,
                        GrowthProject.id == GrowthProjectProvision.growth_project_id,
                    )
                    .where(
                        GrowthProject.status.not_in(('paused', 'archived')),
                        GrowthProjectProvision.attempts < _MAX_AUTOMATIC_ATTEMPTS,
                        sa.or_(
                            sa.and_(
                                GrowthProjectProvision.status == 'failed',
                                GrowthProjectProvision.next_retry_time <= now,
                            ),
                            sa.and_(
                                GrowthProjectProvision.status == 'running',
                                sa.func.coalesce(
                                    GrowthProjectProvision.updated_time,
                                    GrowthProjectProvision.started_time,
                                )
                                <= stale_before,
                            ),
                        ),
                    )
                    .distinct()
                )
            ).scalars()
            return [str(item) for item in ids]


growth_project_provision_service = GrowthProjectProvisionService()
