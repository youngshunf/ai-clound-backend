"""获客商机与成交服务（设计 07 §9）。

立商机（一个客户可多商机）、阶段推进（每次写 stage_change activity，允许倒退）、
成交登记 deal.close（won/lost + 金额 + close_note，复盘留档）。成交是事实登记不是合同动作，
统一授权开关在工具层默认 ask（M4），本服务只做业务落库 + 客户态联动。
"""

from __future__ import annotations

import hashlib
import json

from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model.activity import Activity
from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.growth_attribution_event import (
    GrowthAttributionEvent,
)
from backend.app.hasn_growth.model.growth_project import GrowthProject
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.service.funnel_service import (
    GrowthFunnelService,
    masked_customer_response,
)
from backend.app.hasn_growth.service.pii import redact_pii_value
from backend.app.hasn_growth.service.project_lead_service import project_lead_service
from backend.app.hasn_growth.service.scope_context import GrowthScope, apply_scope
from backend.app.hasn_task.model.task import HasnTask
from backend.common.exception import errors
from backend.utils.timezone import timezone

_OPEN_STAGES = ('contacted', 'replied', 'proposal', 'negotiation')
_CLOSED_STAGES = ('closed_won', 'closed_lost')


def _gen_no(prefix: str) -> str:
    return f'{prefix}{uuid4().hex[:12].upper()}'


def _request_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _event_key(action: str, idempotency_key: str) -> str:
    return f'opportunity:{action}:{idempotency_key.strip()}'


def _opportunity_to_dict(o: Opportunity) -> dict[str, Any]:
    return {
        'id': o.id,
        'growth_project_id': (str(o.growth_project_id) if o.growth_project_id is not None else None),
        'opportunity_no': o.opportunity_no,
        'customer_id': o.customer_id,
        'name': o.name,
        'version': o.version,
        'stage': o.stage,
        'amount': float(o.amount) if o.amount is not None else None,
        'currency': o.currency,
        'probability': float(o.probability) if o.probability is not None else None,
        'expected_close_at': o.expected_close_at,
        'won_at': o.won_at,
        'lost_at': o.lost_at,
        'lost_reason': o.lost_reason,
        'close_note': o.close_note,
        'review_task_id': o.review_task_id,
        'created_by_kind': o.created_by_kind,
        'owner_scope': o.owner_scope,
        'enterprise_id': o.enterprise_id,
        'assignee': o.assignee,
        'created_time': o.created_time,
        'updated_time': o.updated_time,
        'resource_uri': f'hasn://growth/opportunities/{o.id}',
    }


class GrowthOpportunityService:
    """商机/成交，全 user_id 隔离，跨户 → NotFound。"""

    @staticmethod
    async def _load(
        db: AsyncSession,
        *,
        user_id: int,
        opportunity_id: int,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
        write_lock: bool = False,
    ) -> Opportunity:
        conditions = [Opportunity.id == opportunity_id]
        if growth_project_id is not None:
            conditions.append(Opportunity.growth_project_id == growth_project_id)
        stmt = apply_scope(sa.select(Opportunity).where(*conditions), Opportunity, user_id=user_id, scope=scope)
        if write_lock:
            stmt = stmt.with_for_update()
        o = (await db.execute(stmt)).scalar_one_or_none()
        if not o:
            raise errors.NotFoundError(msg='商机不存在或无权访问')
        return o

    @staticmethod
    async def _idempotent_event(
        db: AsyncSession,
        *,
        growth_project_id: UUID,
        event_key: str,
        request_hash: str,
    ) -> GrowthAttributionEvent | None:
        event = (
            await db.execute(
                sa.select(GrowthAttributionEvent).where(
                    GrowthAttributionEvent.growth_project_id == growth_project_id,
                    GrowthAttributionEvent.idempotency_key == event_key,
                )
            )
        ).scalar_one_or_none()
        if event is not None and event.meta_data.get('request_hash') != request_hash:
            raise errors.ConflictError(
                msg='同一幂等键对应了不同商机请求',
                data={'error_code': 'IDEMPOTENCY_PAYLOAD_CONFLICT'},
            )
        return event

    @staticmethod
    def _assert_version(opportunity: Opportunity, expected_version: int | None) -> None:
        if expected_version is None:
            return
        if opportunity.version != expected_version:
            raise errors.ConflictError(
                msg='商机内容已变化，请重新加载后操作',
                data={
                    'error_code': 'OPPORTUNITY_VERSION_CONFLICT',
                    'expected_version': expected_version,
                    'current_version': opportunity.version,
                },
            )

    @staticmethod
    async def _explicit_project(
        db: AsyncSession,
        *,
        growth_project_id: str | UUID | None,
        scope: GrowthScope | None,
    ) -> GrowthProject | None:
        if growth_project_id is None:
            return None
        if scope is None:
            raise errors.RequestError(msg='项目化商机操作缺少权限上下文')
        return await project_lead_service.require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
            require_writable=True,
        )

    @staticmethod
    async def _inherit_project(
        db: AsyncSession,
        *,
        project: GrowthProject | None,
        resource_project_id: UUID | None,
        scope: GrowthScope | None,
    ) -> GrowthProject | None:
        if project is not None or scope is None or resource_project_id is None:
            return project
        return await project_lead_service.require_project(
            db,
            growth_project_id=resource_project_id,
            scope=scope,
            require_writable=True,
        )

    @staticmethod
    def _require_idempotency(
        project: GrowthProject | None,
        idempotency_key: str | None,
    ) -> None:
        if project is not None and not (idempotency_key or '').strip():
            raise errors.RequestError(msg='项目化商机写入必须提供幂等键')

    @classmethod
    def _validate_stage_write(
        cls,
        *,
        project: GrowthProject | None,
        idempotency_key: str | None,
        expected_version: int | None,
        note: str | None,
    ) -> None:
        cls._require_idempotency(project, idempotency_key)
        if project is None:
            return
        if expected_version is None:
            raise errors.RequestError(msg='项目化商机阶段变更必须提供预期版本')
        if not (note or '').strip():
            raise errors.RequestError(msg='项目化商机阶段变更必须填写原因')

    @classmethod
    async def create_opportunity(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int,
        name: str,
        amount: float | None = None,
        currency: str = 'CNY',
        stage: str = 'contacted',
        probability: float | None = None,
        expected_close_at: Any = None,
        created_by_kind: str = 'agent',
        actor_id: str | None = None,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """建立商机，并继承客户归属后写入时间线。"""
        if stage not in _OPEN_STAGES:
            raise errors.RequestError(msg=f'非法商机阶段：{stage}')
        project = await cls._explicit_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        customer = await GrowthFunnelService._load_customer(db, user_id=user_id, customer_id=customer_id, scope=scope)
        project = await cls._inherit_project(
            db,
            project=project,
            resource_project_id=customer.growth_project_id,
            scope=scope,
        )
        if project is not None and customer.growth_project_id != project.id:
            raise errors.NotFoundError(msg='客户不存在或无权访问')
        cls._require_idempotency(project, idempotency_key)

        event_key: str | None = None
        request_hash: str | None = None
        if project is not None and idempotency_key is not None:
            event_key = _event_key('create', idempotency_key)
            request_hash = _request_hash({
                'customer_id': customer_id,
                'name': name.strip(),
                'amount': amount,
                'currency': currency,
                'stage': stage,
                'probability': probability,
                'expected_close_at': expected_close_at,
            })
            await db.execute(
                sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                {'key': f'{project.id}:{event_key}'},
            )
            replay = await cls._idempotent_event(
                db,
                growth_project_id=project.id,
                event_key=event_key,
                request_hash=request_hash,
            )
            if replay is not None and replay.opportunity_id is not None:
                existing = await cls._load(
                    db,
                    user_id=user_id,
                    opportunity_id=replay.opportunity_id,
                    scope=scope,
                    growth_project_id=project.id,
                )
                return _opportunity_to_dict(existing)
        opp = Opportunity(
            opportunity_no=_gen_no('OPP'),
            customer_id=customer_id,
            user_id=user_id,
            growth_project_id=customer.growth_project_id,
            name=name,
            version=1,
            stage=stage,
            amount=Decimal(str(amount)) if amount is not None else None,
            currency=currency,
            probability=Decimal(str(probability)) if probability is not None else None,
            expected_close_at=expected_close_at,
            created_by_kind=created_by_kind,
            owner_scope=customer.owner_scope,
            enterprise_id=customer.enterprise_id,
            assignee=customer.assignee,
        )
        db.add(opp)
        # 新商机会重新激活既有流失客户；已成交和归档客户不回退。
        if customer.lifecycle_status not in ('won', 'archived'):
            customer.lifecycle_status = 'opportunity'
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=customer_id,
            kind='stage_change',
            content=f'立商机「{name}」（阶段 {stage}）',
            opportunity_id=opp.id,
            actor_kind=created_by_kind,
            actor_id=actor_id,
            ref_table='opportunity',
            ref_id=str(opp.id),
        )
        if project is not None and event_key is not None and request_hash is not None:
            db.add(
                GrowthAttributionEvent(
                    growth_project_id=project.id,
                    event_type='opportunity',
                    customer_id=customer.id,
                    opportunity_id=opp.id,
                    source_kind=created_by_kind,
                    source_ref=actor_id,
                    occurred_time=timezone.now(),
                    idempotency_key=event_key,
                    meta_data={
                        'request_hash': request_hash,
                        'stage': stage,
                        'version': opp.version,
                    },
                )
            )
            await db.flush()
        return _opportunity_to_dict(opp)

    @classmethod
    async def update_stage(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        opportunity_id: int,
        stage: str,
        note: str | None = None,
        actor_kind: str = 'agent',
        actor_id: str | None = None,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """推进/倒退商机阶段（允许倒退；每次写 stage_change 谁/何时/为何）。成交收口走 close_deal。"""
        if stage not in _OPEN_STAGES:
            raise errors.RequestError(msg=f'阶段流转仅限 {_OPEN_STAGES}；成交/流失请用 close_deal')
        project = await cls._explicit_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        o = await cls._load(
            db,
            user_id=user_id,
            opportunity_id=opportunity_id,
            scope=scope,
            growth_project_id=project.id if project is not None else growth_project_id,
            write_lock=True,
        )
        project = await cls._inherit_project(
            db,
            project=project,
            resource_project_id=o.growth_project_id,
            scope=scope,
        )
        cls._validate_stage_write(
            project=project,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            note=note,
        )

        event_key: str | None = None
        request_hash: str | None = None
        if project is not None and idempotency_key is not None:
            event_key = _event_key('stage', idempotency_key)
            request_hash = _request_hash({
                'opportunity_id': opportunity_id,
                'stage': stage,
                'note': (note or '').strip(),
                'expected_version': expected_version,
            })
            replay = await cls._idempotent_event(
                db,
                growth_project_id=project.id,
                event_key=event_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return _opportunity_to_dict(o)

        cls._assert_version(o, expected_version)
        if o.stage in _CLOSED_STAGES:
            raise errors.ForbiddenError(msg=f'商机已收口（{o.stage}），不可再改阶段')
        if o.stage == stage:
            raise errors.RequestError(msg='目标阶段与当前阶段相同')
        old = o.stage
        o.stage = stage
        o.version += 1
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=o.customer_id,
            kind='stage_change',
            content=f'阶段 {old} → {stage}' + (f'：{note}' if note else ''),
            opportunity_id=o.id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            ref_table='opportunity',
            ref_id=str(o.id),
        )
        if project is not None and event_key is not None and request_hash is not None:
            db.add(
                GrowthAttributionEvent(
                    growth_project_id=project.id,
                    event_type='opportunity',
                    customer_id=o.customer_id,
                    opportunity_id=o.id,
                    source_kind=actor_kind,
                    source_ref=actor_id,
                    occurred_time=timezone.now(),
                    idempotency_key=event_key,
                    meta_data={
                        'request_hash': request_hash,
                        'from_stage': old,
                        'to_stage': stage,
                        'reason': (note or '').strip(),
                        'version': o.version,
                    },
                )
            )
            await db.flush()
        return _opportunity_to_dict(o)

    @staticmethod
    async def _sync_customer_after_close(
        db: AsyncSession,
        *,
        opportunity: Opportunity,
        customer: Customer,
        result: str,
    ) -> None:
        if result == 'won':
            customer.lifecycle_status = 'won'
            return
        other_stages = list(
            (
                await db.execute(
                    sa.select(Opportunity.stage).where(
                        Opportunity.customer_id == opportunity.customer_id,
                        Opportunity.growth_project_id == opportunity.growth_project_id,
                        Opportunity.id != opportunity.id,
                    )
                )
            ).scalars()
        )
        if 'closed_won' in other_stages:
            customer.lifecycle_status = 'won'
        elif any(stage in _OPEN_STAGES for stage in other_stages):
            customer.lifecycle_status = 'opportunity'
        else:
            customer.lifecycle_status = 'lost'

    @staticmethod
    async def _create_review_task(
        db: AsyncSession,
        *,
        project: GrowthProject,
        customer: Customer,
        opportunity: Opportunity,
        result: str,
        actor_kind: str,
        actor_id: str | None,
    ) -> str:
        task_agent_id = (customer.owner_agent_id or project.owner_agent_id or '').strip()
        if not task_agent_id:
            raise errors.ConflictError(
                msg='获客项目尚未绑定负责分身，无法建立成交复盘任务',
                data={'error_code': 'GROWTH_PROJECT_AGENT_REQUIRED'},
            )
        task_uuid = str(
            uuid5(
                NAMESPACE_URL,
                f'hasn:growth:deal-review:{project.id}:{opportunity.id}:{result}',
            )
        )
        now = timezone.now()
        prompt = (
            f'复盘获客项目 {project.id} 的商机 {opportunity.id}（结果 {result}）。'
            '读取商机、客户、活动、归因和执行版本，分开列出事实、推断与建议；'
            '只生成下一轮 ICP、打法和渠道建议草案，不得自动修改任何已确认画像或打法版本。'
        )
        if customer.followup_task_id:
            await db.execute(
                sa
                .update(HasnTask)
                .where(
                    HasnTask.task_uuid == customer.followup_task_id,
                    HasnTask.project_id == project.platform_project_id,
                    HasnTask.app_id == 'growth',
                    HasnTask.deleted_at.is_(None),
                )
                .values(
                    enabled=False,
                    state='completed',
                    next_run_at=None,
                    updated_time=now,
                )
            )
        await db.execute(
            pg_insert(HasnTask)
            .values(
                owner_id=project.owner_hasn_id,
                agent_id=task_agent_id,
                name=f'复盘商机：{opportunity.name}'[:200],
                description='成交或流失后幂等建立的经营复盘任务',
                prompt=prompt,
                schedule_type='once',
                schedule_config={'run_at': now.isoformat()},
                schedule_display='成交或流失后立即复盘',
                timezone='Asia/Shanghai',
                misfire_policy='skip',
                enabled=True,
                state='scheduled',
                next_run_at=now,
                created_by=actor_id,
                task_uuid=task_uuid,
                executor_policy='local_node',
                task_revision=1,
                created_by_kind=actor_kind,
                risk_level='low',
                project_id=project.platform_project_id,
                app_id='growth',
                execution_kind='freeform',
                execution_spec={'prompt': prompt},
            )
            .on_conflict_do_nothing(index_elements=[HasnTask.task_uuid])
        )
        opportunity.review_task_id = task_uuid
        return task_uuid

    @staticmethod
    def _validate_close_facts(
        *,
        result: str,
        amount: float | None,
        currency: str | None,
        lost_reason: str | None,
    ) -> None:
        if result == 'won' and (amount is None or amount <= 0 or not currency):
            raise errors.RequestError(msg='成交必须填写大于 0 的金额和币种')
        if result == 'lost' and not (lost_reason or '').strip():
            raise errors.RequestError(msg='流失必须填写结构化原因')

    @staticmethod
    def _apply_close_result(
        opportunity: Opportunity,
        *,
        result: str,
        amount: float | None,
        currency: str | None,
        close_note: str | None,
        lost_reason: str | None,
    ) -> None:
        now = timezone.now()
        if result == 'won':
            opportunity.stage = 'closed_won'
            opportunity.won_at = now
            if amount is not None:
                opportunity.amount = Decimal(str(amount))
            if currency is not None:
                opportunity.currency = currency
            opportunity.close_note = close_note
        else:
            opportunity.stage = 'closed_lost'
            opportunity.lost_at = now
            opportunity.lost_reason = lost_reason or close_note
            opportunity.close_note = close_note
        opportunity.version += 1

    @staticmethod
    def _close_activity_content(
        opportunity: Opportunity,
        *,
        result: str,
        close_note: str | None,
        lost_reason: str | None,
    ) -> str:
        if result == 'won':
            suffix = f'：{close_note}' if close_note else ''
            return f'成交「{opportunity.name}」金额 {opportunity.amount or 0} {opportunity.currency}{suffix}'
        return f'流失「{opportunity.name}」：{opportunity.lost_reason or "未注明"}'

    @staticmethod
    async def _add_close_attribution(
        db: AsyncSession,
        *,
        project: GrowthProject,
        opportunity: Opportunity,
        result: str,
        actor_kind: str,
        actor_id: str | None,
        event_key: str,
        request_hash: str,
    ) -> None:
        db.add(
            GrowthAttributionEvent(
                growth_project_id=project.id,
                event_type='closed_won' if result == 'won' else 'closed_lost',
                customer_id=opportunity.customer_id,
                opportunity_id=opportunity.id,
                source_kind=actor_kind,
                source_ref=actor_id,
                amount=opportunity.amount if result == 'won' else None,
                currency=opportunity.currency if result == 'won' else None,
                occurred_time=timezone.now(),
                idempotency_key=event_key,
                meta_data={
                    'request_hash': request_hash,
                    'result': result,
                    'lost_reason': opportunity.lost_reason,
                    'review_task_id': opportunity.review_task_id,
                    'version': opportunity.version,
                },
            )
        )
        await db.flush()

    @classmethod
    async def close_deal(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        opportunity_id: int,
        result: str,
        amount: float | None = None,
        currency: str | None = None,
        close_note: str | None = None,
        lost_reason: str | None = None,
        actor_kind: str = 'agent',
        actor_id: str | None = None,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """成交/流失登记（result='won'|'lost'）。won 入 funnel 金额统计；lost 留败因复盘。"""
        if result not in ('won', 'lost'):
            raise errors.RequestError(msg="result 只能是 'won' 或 'lost'")
        project = await cls._explicit_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        o = await cls._load(
            db,
            user_id=user_id,
            opportunity_id=opportunity_id,
            scope=scope,
            growth_project_id=project.id if project is not None else growth_project_id,
            write_lock=True,
        )
        project = await cls._inherit_project(
            db,
            project=project,
            resource_project_id=o.growth_project_id,
            scope=scope,
        )
        if project is not None:
            cls._require_idempotency(project, idempotency_key)
            if expected_version is None:
                raise errors.RequestError(msg='项目化成交登记必须提供预期版本')
            cls._validate_close_facts(
                result=result,
                amount=amount,
                currency=currency,
                lost_reason=lost_reason,
            )

        event_key: str | None = None
        request_hash: str | None = None
        if project is not None and idempotency_key is not None:
            event_key = _event_key('close', idempotency_key)
            request_hash = _request_hash({
                'opportunity_id': opportunity_id,
                'result': result,
                'amount': amount,
                'currency': currency,
                'close_note': (close_note or '').strip(),
                'lost_reason': (lost_reason or '').strip(),
                'expected_version': expected_version,
            })
            replay = await cls._idempotent_event(
                db,
                growth_project_id=project.id,
                event_key=event_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return _opportunity_to_dict(o)

        cls._assert_version(o, expected_version)
        if o.stage in _CLOSED_STAGES:
            raise errors.ForbiddenError(msg=f'商机已收口（{o.stage}），不可重复登记')
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=o.customer_id,
            scope=scope,
        )
        if project is not None:
            await cls._create_review_task(
                db,
                project=project,
                customer=customer,
                opportunity=o,
                result=result,
                actor_kind=actor_kind,
                actor_id=actor_id,
            )
        cls._apply_close_result(
            o,
            result=result,
            amount=amount,
            currency=currency,
            close_note=close_note,
            lost_reason=lost_reason,
        )
        await db.flush()
        await cls._sync_customer_after_close(
            db,
            opportunity=o,
            customer=customer,
            result=result,
        )
        await db.flush()
        await GrowthFunnelService._add_activity(
            db,
            user_id=user_id,
            customer_id=o.customer_id,
            kind='close',
            content=cls._close_activity_content(
                o,
                result=result,
                close_note=close_note,
                lost_reason=lost_reason,
            ),
            opportunity_id=o.id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            ref_table='opportunity',
            ref_id=str(o.id),
        )
        if project is not None and event_key is not None and request_hash is not None:
            await cls._add_close_attribution(
                db,
                project=project,
                opportunity=o,
                result=result,
                actor_kind=actor_kind,
                actor_id=actor_id,
                event_key=event_key,
                request_hash=request_hash,
            )
        return _opportunity_to_dict(o)

    @classmethod
    async def get_opportunity(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        opportunity_id: int,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        if growth_project_id is not None:
            if scope is None:
                raise errors.RequestError(msg='项目化商机读取缺少权限上下文')
            project = await project_lead_service.require_project(
                db,
                growth_project_id=growth_project_id,
                scope=scope,
            )
            growth_project_id = project.id
        o = await cls._load(
            db,
            user_id=user_id,
            opportunity_id=opportunity_id,
            scope=scope,
            growth_project_id=growth_project_id,
        )
        return _opportunity_to_dict(o)

    @classmethod
    async def get_opportunity_detail(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        growth_project_id: str | UUID,
        opportunity_id: int,
        scope: GrowthScope,
    ) -> dict[str, Any]:
        project = await project_lead_service.require_project(
            db,
            growth_project_id=growth_project_id,
            scope=scope,
        )
        opportunity = await cls._load(
            db,
            user_id=user_id,
            opportunity_id=opportunity_id,
            scope=scope,
            growth_project_id=project.id,
        )
        customer = await GrowthFunnelService._load_customer(
            db,
            user_id=user_id,
            customer_id=opportunity.customer_id,
            scope=scope,
        )
        activities = list(
            (
                await db.execute(
                    sa
                    .select(Activity)
                    .where(
                        Activity.growth_project_id == project.id,
                        Activity.opportunity_id == opportunity.id,
                    )
                    .order_by(Activity.occurred_at.desc(), Activity.id.desc())
                    .limit(100)
                )
            ).scalars()
        )
        attribution = list(
            (
                await db.execute(
                    sa
                    .select(GrowthAttributionEvent)
                    .where(
                        GrowthAttributionEvent.growth_project_id == project.id,
                        GrowthAttributionEvent.opportunity_id == opportunity.id,
                    )
                    .order_by(
                        GrowthAttributionEvent.occurred_time.desc(),
                        GrowthAttributionEvent.id.desc(),
                    )
                    .limit(100)
                )
            ).scalars()
        )
        task_ids = {task_id for task_id in (customer.followup_task_id, opportunity.review_task_id) if task_id}
        tasks: list[HasnTask] = []
        if task_ids:
            tasks = list(
                (
                    await db.execute(
                        sa
                        .select(HasnTask)
                        .where(
                            HasnTask.task_uuid.in_(task_ids),
                            HasnTask.project_id == project.platform_project_id,
                            HasnTask.app_id == 'growth',
                            HasnTask.deleted_at.is_(None),
                        )
                        .order_by(HasnTask.id.desc())
                    )
                ).scalars()
            )
        return {
            'growth_project_id': str(project.id),
            'opportunity': _opportunity_to_dict(opportunity),
            'customer': await masked_customer_response(db, customer),
            'activities': [
                {
                    'id': row.id,
                    'kind': row.kind,
                    'content': redact_pii_value(row.content),
                    'actor_kind': row.actor_kind,
                    'actor_id': row.actor_id,
                    'occurred_at': row.occurred_at,
                }
                for row in activities
            ],
            'tasks': [
                {
                    'task_uuid': row.task_uuid,
                    'name': row.name,
                    'state': row.state,
                    'next_run_at': row.next_run_at,
                    'last_status': row.last_status,
                    'agent_id': row.agent_id,
                }
                for row in tasks
            ],
            'attribution': [
                {
                    'id': row.id,
                    'event_type': row.event_type,
                    'source_kind': row.source_kind,
                    'source_ref': row.source_ref,
                    'amount': float(row.amount) if row.amount is not None else None,
                    'currency': row.currency,
                    'occurred_time': row.occurred_time,
                    'metadata': row.meta_data,
                }
                for row in attribution
            ],
            'resource_uri': f'hasn://growth/opportunities/{opportunity.id}',
        }

    @staticmethod
    async def list_opportunities(
        db: AsyncSession,
        *,
        user_id: int,
        customer_id: int | None = None,
        stage: str | None = None,
        open_only: bool = False,
        assignee: str | None = None,
        limit: int = 50,
        scope: GrowthScope | None = None,
        growth_project_id: str | UUID | None = None,
    ) -> list[dict[str, Any]]:
        if growth_project_id is not None:
            if scope is None:
                raise errors.RequestError(msg='项目化商机读取缺少权限上下文')
            project = await project_lead_service.require_project(
                db,
                growth_project_id=growth_project_id,
                scope=scope,
            )
            growth_project_id = project.id
        stmt = apply_scope(sa.select(Opportunity), Opportunity, user_id=user_id, scope=scope)
        if growth_project_id is not None:
            stmt = stmt.where(Opportunity.growth_project_id == growth_project_id)
        if assignee and scope is not None and scope.is_enterprise:
            stmt = stmt.where(Opportunity.assignee == assignee)
        if customer_id is not None:
            stmt = stmt.where(Opportunity.customer_id == customer_id)
        if stage is not None:
            stmt = stmt.where(Opportunity.stage == stage)
        if open_only:
            stmt = stmt.where(Opportunity.stage.in_(_OPEN_STAGES))
        stmt = stmt.order_by(Opportunity.id.desc()).limit(min(limit, 200))
        rows = (await db.execute(stmt)).scalars().all()
        return [_opportunity_to_dict(o) for o in rows]


growth_opportunity_service = GrowthOpportunityService()
