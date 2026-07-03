"""获客企业化 GE2 查询层角色裁剪真实 HTTP E2E（零 mock，回滚）。

两 owner 同企业 + 一个个人 owner，真实 PG（DATABASE_PORT=15432）。覆盖 §92 GE2 验收：
- 经理（企业 owner/admin）view=team 列出全企业客户、view=mine 仅自己 assignee；
- 销售（普通 member）即便 view=team 也只列到自己 assignee 的；
- 销售改非自己负责的客户被拒（403）；经理可分配/转移（级联）；
- 个人模式 owner 不受影响（只见自己个人池，不见企业客户）；
- 经理按 assignee 过滤；list 出参带 scope 元信息。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.api.v1.app.growth import router as app_growth_router
from backend.app.hasn_growth.model.customer import Customer
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(app_growth_router, prefix='/api/v1/growth/app')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:  # noqa: RUF029
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


def _ok(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {'code', 'msg', 'data'}, body
    assert body['code'] == 200, body
    return body['data']


@pytest_asyncio.fixture
async def ent() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    ent_id = 7700000 + int(uuid.uuid4().int % 90000)
    base_uid = 950000 + int(uuid.uuid4().int % 9000)

    mgr_uid, sales_uid, solo_uid = base_uid, base_uid + 1, base_uid + 2
    mgr = f'h_mgr_{tag}'
    sales = f'h_sal_{tag}'
    solo = f'h_solo_{tag}'

    # 三个主人（HasnHumans 供 resolve_growth_scope 按 user_id 反查 hasn_id）。
    session.add(HasnHumans(hasn_id=mgr, star_id=f's_{mgr_uid}', user_id=mgr_uid, nickname='经理', status='active'))
    session.add(HasnHumans(hasn_id=sales, star_id=f's_{sales_uid}', user_id=sales_uid, nickname='销售', status='active'))
    session.add(HasnHumans(hasn_id=solo, star_id=f's_{solo_uid}', user_id=solo_uid, nickname='个体', status='active'))

    # 企业成员关系：经理=owner、销售=member（均 approved）。个人 owner 不入企业。
    session.add(HasnEnterpriseMembership(enterprise_id=ent_id, user_id=mgr_uid, role='owner', status='approved'))
    session.add(HasnEnterpriseMembership(enterprise_id=ent_id, user_id=sales_uid, role='member', status='approved'))

    # active_enterprise_id：经理 + 销售指向同一企业；个人 owner 留 null（个人上下文）。
    session.add(HasnOwnerWorkbenchPref(owner_hasn_id=mgr, active_enterprise_id=ent_id))
    session.add(HasnOwnerWorkbenchPref(owner_hasn_id=sales, active_enterprise_id=ent_id))
    session.add(HasnOwnerWorkbenchPref(owner_hasn_id=solo, active_enterprise_id=None))

    # 企业客户：一个 assignee=经理、一个 assignee=销售。个人客户：solo 个人池。
    cust_mgr = Customer(
        customer_no=f'CM{tag}', user_id=mgr_uid, source_kind='manual', company_name='企业客户·经理名下',
        lifecycle_status='active', intent_score=90, owner_scope='enterprise', enterprise_id=ent_id, assignee=mgr,
    )
    cust_sales = Customer(
        customer_no=f'CS{tag}', user_id=sales_uid, source_kind='manual', company_name='企业客户·销售名下',
        lifecycle_status='active', intent_score=80, owner_scope='enterprise', enterprise_id=ent_id, assignee=sales,
    )
    cust_solo = Customer(
        customer_no=f'CP{tag}', user_id=solo_uid, source_kind='manual', company_name='个人客户',
        lifecycle_status='active', intent_score=70, owner_scope='personal', enterprise_id=None, assignee=None,
    )
    session.add_all([cust_mgr, cust_sales, cust_solo])
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    current = SimpleNamespace(uid=mgr_uid, hasn=mgr)

    async def _owner_auth(request: Request) -> None:  # noqa: RUF029
        request.scope['user'] = SimpleNamespace(id=current.uid, hasn_id=current.hasn)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _owner_auth

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, current=current,
            ent_id=ent_id, mgr=mgr, sales=sales, solo=solo,
            mgr_uid=mgr_uid, sales_uid=sales_uid, solo_uid=solo_uid,
            cid_mgr=cust_mgr.id, cid_sales=cust_sales.id, cid_solo=cust_solo.id,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _as(ent: SimpleNamespace, uid: int, hasn: str) -> None:
    ent.current.uid = uid
    ent.current.hasn = hasn


async def test_manager_sees_all_member_sees_own(ent) -> None:
    c = ent.client
    O = '/api/v1/growth/app'

    # --- 经理 view=team：看全企业（含销售名下），不含个人客户 ---
    _as(ent, ent.mgr_uid, ent.mgr)
    data = _ok(await c.get(f'{O}/customers', params={'view': 'team'}))
    assert data['scope']['owner_scope'] == 'enterprise' and data['scope']['viewer_role'] == 'manager'
    ids = {x['id'] for x in data['items']}
    assert ent.cid_mgr in ids and ent.cid_sales in ids
    assert ent.cid_solo not in ids  # 个人客户不进企业列表
    for x in data['items']:
        assert x['owner_scope'] == 'enterprise' and x['enterprise_id'] == ent.ent_id

    # --- 经理 view=mine：仅自己 assignee ---
    mine = _ok(await c.get(f'{O}/customers', params={'view': 'mine'}))
    mine_ids = {x['id'] for x in mine['items']}
    assert mine_ids == {ent.cid_mgr}

    # --- 经理按 assignee 过滤 = 销售 → 仅销售名下 ---
    by_sales = _ok(await c.get(f'{O}/customers', params={'view': 'team', 'assignee': ent.sales}))
    assert {x['id'] for x in by_sales['items']} == {ent.cid_sales}


async def test_member_only_own_even_team(ent) -> None:
    c = ent.client
    O = '/api/v1/growth/app'

    # --- 销售即便传 view=team，也恒只见自己 assignee 的（后端裁剪，前端意图不越权） ---
    _as(ent, ent.sales_uid, ent.sales)
    data = _ok(await c.get(f'{O}/customers', params={'view': 'team'}))
    assert data['scope']['viewer_role'] == 'member'
    assert {x['id'] for x in data['items']} == {ent.cid_sales}

    # --- 销售拿不到经理名下客户详情（不在其裁剪范围）---
    miss = await c.get(f'{O}/customers/{ent.cid_mgr}')
    assert miss.status_code == 404, miss.text


async def test_member_reassign_forbidden_manager_allowed(ent) -> None:
    c = ent.client
    O = '/api/v1/growth/app'

    # --- 销售分配/转移负责人 → 403（仅经理可分配）---
    _as(ent, ent.sales_uid, ent.sales)
    denied = await c.post(f'{O}/customers/{ent.cid_sales}/reassign', json={'assignee': ent.mgr})
    assert denied.status_code == 403, denied.text

    # --- 经理把销售名下客户转给自己 → 成功 ---
    _as(ent, ent.mgr_uid, ent.mgr)
    moved = _ok(await c.post(f'{O}/customers/{ent.cid_sales}/reassign', json={'assignee': ent.mgr}))
    assert moved['assignee'] == ent.mgr

    # --- 转移后：销售 view=team 已看不到该客户；经理仍看到（现两单都归自己）---
    _as(ent, ent.sales_uid, ent.sales)
    sales_list = _ok(await c.get(f'{O}/customers', params={'view': 'team'}))
    assert ent.cid_sales not in {x['id'] for x in sales_list['items']}

    _as(ent, ent.mgr_uid, ent.mgr)
    mgr_mine = _ok(await c.get(f'{O}/customers', params={'view': 'mine'}))
    assert {ent.cid_mgr, ent.cid_sales} <= {x['id'] for x in mgr_mine['items']}


async def test_personal_owner_unaffected(ent) -> None:
    c = ent.client
    O = '/api/v1/growth/app'

    # --- 个人 owner（无 active_enterprise）：只见自己个人池，不见任何企业客户 ---
    _as(ent, ent.solo_uid, ent.solo)
    data = _ok(await c.get(f'{O}/customers', params={'view': 'team'}))
    assert data['scope']['owner_scope'] == 'personal' and data['scope']['viewer_role'] == 'owner'
    ids = {x['id'] for x in data['items']}
    assert ent.cid_solo in ids
    assert ent.cid_mgr not in ids and ent.cid_sales not in ids

    # --- 个人 owner 拿不到企业客户详情 ---
    miss = await c.get(f'{O}/customers/{ent.cid_mgr}')
    assert miss.status_code == 404, miss.text

    # --- 个人 owner 无分配权（非企业经理）→ 403 ---
    denied = await c.post(f'{O}/customers/{ent.cid_solo}/reassign', json={'assignee': ent.mgr})
    assert denied.status_code == 403, denied.text


async def test_report_funnel_scoped(ent) -> None:
    c = ent.client
    O = '/api/v1/growth/app'

    # 经理 team 漏斗「跟进中」计入两单企业客户；mine 只计自己一单。
    _as(ent, ent.mgr_uid, ent.mgr)
    team = _ok(await c.get(f'{O}/report/funnel', params={'view': 'team'}))
    mine = _ok(await c.get(f'{O}/report/funnel', params={'view': 'mine'}))
    assert team['following'] >= 2
    assert mine['following'] >= 1 and mine['following'] < team['following']
