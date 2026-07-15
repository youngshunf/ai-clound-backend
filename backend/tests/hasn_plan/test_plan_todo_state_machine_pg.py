"""PLAN-LOOP L1（修 G3）：待办八态状态机 + P6-C 完成闸 真实 PG 矩阵验证。

零 mock：真实本地 PostgreSQL(15432) 跑 PlanService.update_todo（app 端点 / agent 工具**共用的单点后端缝**）；
事务回滚不污染库。需要：export DATABASE_PORT=15432。

覆盖（[06] §3.3 pytest 项「矩阵逐格 + 闸两态」）：
- **合法流转全过**：遍历 `_TODO_TRANSITIONS` 每条合法边（含 §5.3 放宽：inbox/todo/scheduled → done）；
- **非法流转全拒**：遍历补集，全部 `invalid_status_transition`；未知目标态亦拒；
- **P6-C 完成闸两态**：output_spec.required + 无匹配 kind active 产物 → 拒 `output_not_satisfied`；
  有匹配产物 → 放行（且自动补 completed_time）；非 required / 无 spec → 放行；override 强制放行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn_plan.service import origin_ref as oref
from backend.app.hasn_plan.service.plan_app_service import (
    _TODO_STATUSES,
    _TODO_TRANSITIONS,
    PlanService,
)
from backend.common.exception import errors
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _owner() -> str:
    return f'hasnOwner_{uuid4().hex[:18]}'


def test_agent_seam_has_no_override_default() -> None:
    """单点接缝铁律：update_todo 默认 override_output_gate=False——分身工具不传即被闸约束。

    守卫「分身端不该拿到 override」：默认参数必须是 False（人类端点才显式传 True）。纯签名内省，无 DB。
    """
    import inspect

    sig = inspect.signature(PlanService.update_todo)
    assert sig.parameters['override_output_gate'].default is False


async def _seed_artifact(db: AsyncSession, *, owner: str, todo_id: int, kind: str, status: str = 'active') -> None:
    """按权威 origin_ref=resource:plan:todo:{id}（L0 冒号形）登记一条产物，喂 P6-C 反查。"""
    db.add(
        HasnArtifacts(
            artifact_id=f'art_{uuid4().hex[:20]}',
            agent_hasn_id=f'hasnAgent_{uuid4().hex[:14]}',
            owner_hasn_id=owner,
            kind=kind,
            title='交付产物',
            origin_ref=oref.todo_ref(todo_id),
            source_kind='app',  # doc35 §5：`tool_output` 已砍
            status=status,
        )
    )
    await db.flush()


# ── L1-a：状态机矩阵（合法全过 / 非法全拒）────────────────────────────────────
async def test_legal_transitions_all_pass() -> None:
    """遍历状态机每条合法边：create 到 from 态（create 不过机），update 到 to 态应成功落库。

    无 output_spec → 完成闸直过，故 →done 边亦成功（验证 §5.3 放宽同时不误伤闸）。
    """
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            for frm, tos in _TODO_TRANSITIONS.items():
                for to in sorted(tos):
                    t = await svc.create_todo(db, owner=owner, data={'title': f'{frm}->{to}', 'status': frm})
                    res = await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': to})
                    assert res['status'] == to, f'合法边 {frm}->{to} 未落库'
        finally:
            await db.rollback()


async def test_illegal_transitions_all_rejected() -> None:
    """遍历补集（全状态对 - 合法边 - 自反）：每格必 raise invalid_status_transition。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            for frm in sorted(_TODO_STATUSES):
                legal = _TODO_TRANSITIONS[frm] | {frm}  # 自反是 no-op（target==现值→跳过校验），不算非法
                for to in sorted(_TODO_STATUSES - legal):
                    t = await svc.create_todo(db, owner=owner, data={'title': f'{frm}->{to}', 'status': frm})
                    with pytest.raises(errors.RequestError) as ei:
                        await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': to})
                    assert ei.value.data['error_code'] == 'invalid_status_transition', f'{frm}->{to}'
        finally:
            await db.rollback()


async def test_unknown_target_status_rejected() -> None:
    """未知目标态（不在八态内）→ invalid_status_transition（守卫先拦未知，再拦非法边）。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(db, owner=owner, data={'title': '未知态', 'status': 'todo'})
            with pytest.raises(errors.RequestError) as ei:
                await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'archived'})
            assert ei.value.data['error_code'] == 'invalid_status_transition'
        finally:
            await db.rollback()


async def test_self_transition_is_noop() -> None:
    """自反流转（doing→doing）不报错、不改动——target==现值即跳过状态机校验。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(db, owner=owner, data={'title': '自反', 'status': 'doing', 'notes': '原备注'})
            res = await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'doing', 'notes': '改备注'})
            assert res['status'] == 'doing'
            assert res['notes'] == '改备注'  # 其它字段照常更新
        finally:
            await db.rollback()


# ── L1-b：P6-C 完成闸两态 ─────────────────────────────────────────────────────
# doc35 §0.2 新契约：非应用资源按载体判 → `artifact_kind`（`kind` 是已退役的旧键）。
_SPEC_DOC = {'required': True, 'expects': [{'artifact_kind': 'document', 'format': 'markdown'}]}


async def test_gate_required_no_artifact_rejects_done() -> None:
    """required + 无产物 → 拒置 done，回 output_not_satisfied（杜绝分身假完成/零 fake 红线）。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(
                db, owner=owner, data={'title': '写报告', 'status': 'doing', 'output_spec': _SPEC_DOC, 'notes': 'x'}
            )
            with pytest.raises(errors.RequestError) as ei:
                await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'done'})
            assert ei.value.data['error_code'] == 'output_not_satisfied'
            # 未落库：状态仍停在 doing（异常前不改状态）
            assert (await svc.get_todo(db, owner=owner, pk=t['id']))['status'] == 'doing'
        finally:
            await db.rollback()


async def test_gate_matching_artifact_allows_done() -> None:
    """required + 有匹配 kind 的 active 产物 → 放行置 done，且自动补 completed_time。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(
                db, owner=owner, data={'title': '写报告', 'status': 'doing', 'output_spec': _SPEC_DOC, 'notes': 'x'}
            )
            await _seed_artifact(db, owner=owner, todo_id=int(t['id']), kind='document')
            res = await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'done'})
            assert res['status'] == 'done'
            assert res['completed_time'] is not None  # 完成落库自动补时间
        finally:
            await db.rollback()


async def test_gate_wrong_kind_artifact_rejects_done() -> None:
    """required document 但只有 image 产物（kind 不匹配）→ 仍拒（不是「有任意产物就算」）。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(
                db, owner=owner, data={'title': '写报告', 'status': 'doing', 'output_spec': _SPEC_DOC, 'notes': 'x'}
            )
            await _seed_artifact(db, owner=owner, todo_id=int(t['id']), kind='image')
            with pytest.raises(errors.RequestError) as ei:
                await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'done'})
            assert ei.value.data['error_code'] == 'output_not_satisfied'
        finally:
            await db.rollback()


async def test_gate_deleted_artifact_does_not_satisfy() -> None:
    """有匹配 kind 但 status='deleted' 的产物 → 不算满足（list_by_origin 只认 active）。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(
                db, owner=owner, data={'title': '写报告', 'status': 'doing', 'output_spec': _SPEC_DOC, 'notes': 'x'}
            )
            await _seed_artifact(db, owner=owner, todo_id=int(t['id']), kind='document', status='deleted')
            with pytest.raises(errors.RequestError) as ei:
                await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'done'})
            assert ei.value.data['error_code'] == 'output_not_satisfied'
        finally:
            await db.rollback()


async def test_gate_not_required_allows_done() -> None:
    """非 required（或无 output_spec）→ 完成闸直过，无需产物。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            # 无 output_spec
            t1 = await svc.create_todo(db, owner=owner, data={'title': '去健身', 'status': 'doing'})
            assert (await svc.update_todo(db, owner=owner, pk=t1['id'], data={'status': 'done'}))['status'] == 'done'
            # required=False
            t2 = await svc.create_todo(
                db,
                owner=owner,
                data={'title': '随手记', 'status': 'doing', 'output_spec': {'required': False, 'expects': []}},
            )
            assert (await svc.update_todo(db, owner=owner, pk=t2['id'], data={'status': 'done'}))['status'] == 'done'
        finally:
            await db.rollback()


async def test_gate_override_bypasses() -> None:
    """override_output_gate=True（仅人类端点传，主人裁量强制完成）→ required+无产物仍放行。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            t = await svc.create_todo(
                db, owner=owner, data={'title': '写报告', 'status': 'doing', 'output_spec': _SPEC_DOC, 'notes': 'x'}
            )
            res = await svc.update_todo(db, owner=owner, pk=t['id'], data={'status': 'done'}, override_output_gate=True)
            assert res['status'] == 'done'
        finally:
            await db.rollback()
