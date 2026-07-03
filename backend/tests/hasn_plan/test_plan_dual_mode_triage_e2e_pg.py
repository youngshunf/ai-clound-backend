"""PLAN-ENT + PLAN-TRIAGE 场景化进程内 E2E（施工清单 实施/02 §E「两-owner / 进程内」的进程内臂）。

零 mock：真实本地 PostgreSQL(15432)，直调分身工具 handler（`backend.app.mcp.tools.plan`）
→ 权威 service（`plan_service`），一条事务里把 §E 的双模化 + 到期分诊场景当作**一条端到端流程**
连续走通（非孤立单元断言），事务回滚不污染库。需要：export DATABASE_PORT=15432。

覆盖 §E 场景（云端可跑臂，[04] §5/§6 + [01] §7.5 + [05] §11）：
- ① 企业会议闭环：owner A 企业空间建会议拉 B（event.create scope=enterprise + 组织者行自动落）
  → B 被邀即上日历（attendee 行）→ B RSVP 接受；
- ② 忙闲两层可见性：同部门同事只回匿名忙碌块（不泄标题）/ 跨部门 + 非成员全隐 / 被邀人是参与者（可 RSVP=有访问）；
- ③ 个人不串企业：今日「个人组」恒 enterprise_id IS NULL、「企业组」恒 =E，两条独立读并集不交叉；
- ④ PE-7：分身 scope=enterprise 但不在企业空间 → 诚实拒绝 not_in_enterprise_space、零落库；
- ⑤ OA→plan 注入：event.create(source=oa_meeting, origin_ref) → 事件带来源 + origin_ref 可反查 + 组织者行；
- ⑥ 到期分诊 owner_decision（云端臂）：分身落 actor=owner_decision（不被 notes_required 误拦）→ decision_note 留痕往返。

真机臂（两-owner 真实 hermes runtime 派发 → 提问卡 → 主人决策回灌 → 完成投影）infra-gated，
见 [04] §9 PE-e 活体清单；本文件覆盖不依赖真实 runtime / 第二台设备的全部云端 E2E。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_enterprise_member_role import HasnEnterpriseMemberRole
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_enterprise_role import HasnEnterpriseRole
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_plan.model import Todo
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.plan import (
    ERR_NOT_IN_ENTERPRISE_SPACE,
    _h_availability,
    _h_create_event,
    _h_create_todo,
    _h_event_rsvp,
)
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_DAY_START = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
_DAY_END = datetime(2026, 7, 2, 23, 59, tzinfo=timezone.utc)
_T0 = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)


def _ctx(owner_hasn_id: str, owner_uid: int, *, name: str = '规划分身') -> AgentContext:
    return AgentContext(
        hasn_id=f'a_{uuid4().hex[:16]}',
        owner_id=owner_uid,
        agent_status='active',
        metadata={},
        agent_name=name,
        owner_hasn_id=owner_hasn_id,
    )


async def _seed(db) -> dict:  # noqa: ANN001
    """企业 E + 销售/工程两部门；A(organizer,活跃E) B(同部门,活跃E) C(工程部) loner(无企业身份)。"""
    base = 720_000_000 + (uuid4().int % 90_000_000)
    e_id = base + 10
    ua, ub, uc, ul = base, base + 1, base + 2, base + 3
    ha, hb, hc, hl = (f'h_{uuid4().hex[:16]}' for _ in range(4))
    for hasn_id, uid in ((ha, ua), (hb, ub), (hc, uc), (hl, ul)):
        db.add(HasnHumans(hasn_id=hasn_id, user_id=uid, star_id=str(uid), nickname=hasn_id, status='active'))
    for uid in (ua, ub, uc):
        db.add(HasnEnterpriseMembership(enterprise_id=e_id, user_id=uid, role='member', status='approved'))
    sales = HasnEnterpriseRole(enterprise_id=e_id, name='销售部', kind='department')
    eng = HasnEnterpriseRole(enterprise_id=e_id, name='工程部', kind='department')
    db.add_all([sales, eng])
    await db.flush()
    db.add_all([
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=ua, role_id=sales.id),
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=ub, role_id=sales.id),
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=uc, role_id=eng.id),
    ])
    # A、B 都在企业空间活跃（B 用于「被邀即上企业今日」验证）
    db.add(HasnOwnerWorkbenchPref(owner_hasn_id=ha, active_enterprise_id=e_id))
    db.add(HasnOwnerWorkbenchPref(owner_hasn_id=hb, active_enterprise_id=e_id))
    await db.flush()
    return {
        'e_id': e_id, 'sales_id': sales.id,
        'A': ha, 'A_uid': ua, 'B': hb, 'B_uid': ub, 'C': hc, 'C_uid': uc, 'loner': hl, 'loner_uid': ul,
    }


async def test_plan_ent_triage_full_stack_e2e() -> None:
    """§E 双模化 + 到期分诊：单事务连续走通 ①→⑥（云端可跑臂），真实 PG，零 mock。"""
    async with async_db_session() as db:
        try:
            w = await _seed(db)
            ctx_a = _ctx(w['A'], w['A_uid'], name='A的规划分身')
            ctx_b = _ctx(w['B'], w['B_uid'], name='B的规划分身')

            # ── ① 企业会议闭环：A 企业空间建会议拉 B → 组织者行自动落 → B 被邀即上日历 → B RSVP 接受 ──
            ev = await _h_create_event(
                db, ctx_a,
                {'title': '季度评审会', 'start_at': _T0, 'end_at': _T1, 'scope': 'enterprise', 'attendees': [w['B']]},
            )
            assert ev.get('enterprise_id') == w['e_id'], '① 企业事件应落企业归属'
            eid = int(ev['id'])
            attendees = {a['attendee_hasn_id']: a for a in await plan_service.list_attendees(db, event_id=eid)}
            org, inv = attendees[w['A']], attendees[w['B']]
            assert org['role'] == 'organizer' and org['rsvp'] == 'accepted', '① 组织者行自动落'
            assert inv['role'] == 'required' and inv['rsvp'] == 'none', '① B 被邀即上日历'
            rsvp = await _h_event_rsvp(db, ctx_b, {'event_id': eid, 'rsvp': 'accepted'})
            assert rsvp['rsvp'] == 'accepted' and rsvp['attendee_hasn_id'] == w['B'], '① B 作为参与者可 RSVP'

            # ── ② 忙闲两层可见性：B（同部门）与 C（工程部）各建私密企业事件；A 查 B/C/loner 忙闲 ──
            await plan_service.create_event(
                db, owner=w['B'],
                data={'title': 'B 的私密方案会', 'start_at': _T0, 'end_at': _T1, 'visibility': 'private'},
                enterprise_id=w['e_id'], dept_id=w['sales_id'],
            )
            await plan_service.create_event(
                db, owner=w['C'],
                data={'title': 'C 的私密评审', 'start_at': _T0, 'end_at': _T1, 'visibility': 'private'},
                enterprise_id=w['e_id'], dept_id=None,
            )
            avail = await _h_availability(
                db, ctx_a, {'enterprise_id': w['e_id'], 'members': [w['B'], w['C'], w['loner']]}
            )
            assert w['B'] in avail and avail[w['B']][0]['title'] == '忙碌', '② 同部门只回匿名忙碌块（不泄标题）'
            assert avail[w['B']][0].get('busy') is True
            assert w['C'] not in avail, '② 跨部门（超数据范围）全隐'
            assert w['loner'] not in avail, '② 非成员全隐'

            # ── ③ 个人不串企业：A 建个人待办（无 scope）+ 企业待办（scope=enterprise）→ 今日两组独立不交叉 ──
            personal = await _h_create_todo(db, ctx_a, {'title': 'A 个人：读书'})
            assert personal.get('enterprise_id') is None, '③ 个人待办 enterprise_id 恒 NULL'
            ent_todo = await _h_create_todo(db, ctx_a, {'title': 'A 企业：写周报', 'scope': 'enterprise'})
            assert ent_todo.get('enterprise_id') == w['e_id'] and ent_todo.get('dept_id') == w['sales_id']
            today = await plan_service.today_overview(db, owner=w['A'], day_start=_DAY_START, day_end=_DAY_END)
            personal_inbox = {t['id'] for t in today['inbox']}
            assert personal['id'] in personal_inbox, '③ 个人组含个人待办'
            assert ent_todo['id'] not in personal_inbox, '③ 企业待办不串进个人组'
            ent_group = today['enterprise']
            assert ent_group is not None and ent_group.get('enterprise_id') == w['e_id'], '③ 企业组存在'

            # ── ④ PE-7：loner scope=enterprise 但不在企业空间 → 诚实拒绝 + 零落库 ──
            ctx_loner = _ctx(w['loner'], w['loner_uid'])
            rej = await _h_create_todo(db, ctx_loner, {'title': '越权企业待办', 'scope': 'enterprise'})
            assert rej.get('ok') is False and rej.get('error_code') == ERR_NOT_IN_ENTERPRISE_SPACE, '④ 诚实拒绝'
            cnt = (
                await db.execute(sa.select(sa.func.count()).select_from(Todo).where(Todo.owner_hasn_id == w['loner']))
            ).scalar_one()
            assert cnt == 0, '④ 拒绝后零落库'

            # ── ⑤ OA→plan 注入：event.create(source=oa_meeting, origin_ref) → 带来源可反查 + 组织者行 ──
            origin_ref = f'oa:room_booking:{uuid4().hex[:8]}'
            oa_ev = await _h_create_event(
                db, ctx_a,
                {
                    'title': '客户面谈（OA 会议室）', 'start_at': _T0, 'end_at': _T1, 'scope': 'enterprise',
                    'kind': 'fixed', 'source': 'oa_meeting', 'origin_ref': origin_ref, 'attendees': [w['B']],
                },
            )
            assert oa_ev.get('source') == 'oa_meeting', '⑤ 来源落库'
            assert oa_ev.get('origin_ref') == origin_ref, '⑤ origin_ref 落库'
            found = await plan_service.list_enterprise_events(
                db, viewer_owner_hasn_id=w['A'], enterprise_id=w['e_id']
            )
            hit = next((e for e in found if int(e['id']) == int(oa_ev['id'])), None)
            assert hit is not None, '⑤ OA 注入事件可被企业空间读回（origin_ref 反查锚）'
            assert hit.get('origin_ref') == origin_ref, '⑤ 组织者读回见 origin_ref（全详情）'
            oa_rows = await plan_service.list_attendees(db, event_id=int(oa_ev['id']))
            oa_attendees = {a['attendee_hasn_id']: a for a in oa_rows}
            assert oa_attendees[w['A']]['role'] == 'organizer', '⑤ OA 注入事件组织者行自动落'

            # ── ⑥ 到期分诊 owner_decision（云端臂）：分身落 owner_decision 不被 notes 拦 → decision_note 留痕往返 ──
            decision = await _h_create_todo(db, ctx_a, {'title': '需你拍板：批不批这单预算', 'actor': 'owner_decision'})
            assert 'id' in decision, '⑥ owner_decision 放行（非 notes_required 误拦）'
            assert decision.get('actor') == 'owner_decision', '⑥ actor 落 owner_decision'
            updated = await plan_service.update_todo(
                db, owner=w['A'], pk=int(decision['id']), data={'decision_note': '已批：预算 5 万，Q3 执行'}
            )
            assert updated.get('decision_note') == '已批：预算 5 万，Q3 执行', '⑥ decision_note 决策留痕往返'
        finally:
            await db.rollback()
