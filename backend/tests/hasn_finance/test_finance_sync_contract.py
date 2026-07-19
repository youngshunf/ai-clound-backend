"""FIN-P3a — 金融投研 6 类产物 :sync 同事务契约 真实 PG 验证（05 §5.3a / §7）。

零 mock：真实本地 PostgreSQL(15432) 直调 `finance_sync_service.sync_product`，断言
05 §5.3a 同事务契约与 §7 必测契约（云端可覆盖部分）：

- 契约1（严格登记 + 多资源回指）：create/update 登记 hasn_artifacts，origin_ref 必为
  `resource:finance:{ref_type}:{server_id}`；delete 软删该 owner/uri 全部 active 指针；
  主人手建（agent 空）跳过登记但业务行照写；伪造 owner → 404 零写入。
- 契约4（挂靠）：strategy/shadow 两容器 adapter 按 domain 注册、attach_column/is_container 正确。
- 契约5（分享）：finance.strategy 服务端硬拒（不能只藏 UI）。
- 契约8（跨设备/冲突）：create 幂等回放（同 local_ref 不重复铸）；update revision+1；
  base_revision 落后 → 409 ConflictError（带服务端快照）。

需要：本地 PG huanxing@15432 且 hasn_finance 7 表已建（export DATABASE_PORT=15432）。
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.hasn.service.resource_share_service import ResourceShareService, _NON_SHAREABLE_RESOURCE_TYPES
from backend.app.hasn_finance.model.research_report import ResearchReport
from backend.app.hasn_finance.service.finance_sync_service import finance_sync_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

# 六类产物 → (ref_type, uri_domain)（05 §1.1 稳定值，事实源 manifest._FINANCE_RESOURCES）
_FIN = {
    'finance.research_report': ('research', 'finance/reports'),
    'finance.strategy': ('strategy', 'finance/strategies'),
    'finance.backtest_report': ('backtest', 'finance/backtests'),
    'finance.trade_review': ('review', 'finance/reviews'),
    'finance.shadow_account': ('shadow', 'finance/shadow'),
    'finance.watch_briefing': ('briefing', 'finance/briefings'),
}


# ============================ 纯 Python（无 DB）============================


def test_finance_six_descriptors_uri_and_ref_type() -> None:
    """6 类产物 descriptor 全部解析；ref_type / build_uri 与 05 §1.1 一致。"""
    for kind, (ref_type, domain) in _FIN.items():
        d = ai_native_app_registry.resource_descriptor('finance', kind)
        assert d is not None, f'{kind} descriptor 缺失'
        assert d.ref_type == ref_type, f'{kind} ref_type={d.ref_type} 期望 {ref_type}'
        assert d.build_uri('42') == f'hasn://{domain}/42', f'{kind} URI 漂移'


def test_finance_origin_ref_is_multi_resource_shape() -> None:
    """契约1 多资源回指：origin_ref 恒为 resource:finance:{ref_type}:{server_id}（唯一拼接点）。"""
    for kind, (ref_type, _domain) in _FIN.items():
        d = ai_native_app_registry.resource_descriptor('finance', kind)
        origin = hasn_artifacts_service._build_origin_ref(d, app_id='finance', server_id='7')
        assert origin == f'resource:finance:{ref_type}:7', f'{kind} origin_ref={origin}'


def test_finance_two_container_linkage_adapters_registered() -> None:
    """契约4：strategy/shadow 两容器 adapter 已注册，attach_column=platform_project_id、is_container。"""
    for domain, model_name in [('finance/strategies', 'Strategy'), ('finance/shadow', 'ShadowAccount')]:
        a = project_linkage_registry.get(domain)
        assert a is not None, f'{domain} 未注册 linkage adapter'
        assert a.attach_column == 'platform_project_id'
        assert a.owner_column == 'owner_id'
        assert a.is_container is True
        assert a.model.__name__ == model_name
    conts = {a.domain for a in project_linkage_registry.container_adapters()}
    assert {'finance/strategies', 'finance/shadow'} <= conts


def test_finance_strategy_declared_non_shareable() -> None:
    """契约5：finance.strategy 进永不可分享清单（策略含可执行代码）。"""
    assert 'finance.strategy' in _NON_SHAREABLE_RESOURCE_TYPES


# ============================ 真实 PostgreSQL ============================


async def _seed_owner(db) -> str:  # noqa: ANN001
    uid = 740_000_000 + (uuid4().int % 100_000_000)
    hid = f'h_{uuid4().hex[:16]}'
    db.add(HasnHumans(hasn_id=hid, user_id=uid, star_id=str(uid), nickname=hid, status='active'))
    await db.flush()
    return hid


def _report_fields() -> dict:
    return {
        'findings_json': [{'point': '毛利率提升'}],
        'data_as_of': date(2026, 7, 17),
        'usage_json': {'tokens': 0},
        'title': '贵州茅台投研',
        'symbol': '600519',
    }


async def _active_artifacts(db, *, owner: str, uri: str) -> list[HasnArtifacts]:  # noqa: ANN001
    return list(
        (
            await db.execute(
                sa.select(HasnArtifacts).where(
                    HasnArtifacts.owner_hasn_id == owner,
                    HasnArtifacts.resource_uri == uri,
                    HasnArtifacts.status == 'active',
                )
            )
        )
        .scalars()
        .all()
    )


async def test_sync_create_registers_artifact_with_multi_resource_origin_ref() -> None:
    """create（分身参与）→ 业务行 revision=1 + hasn_artifacts 指针（origin_ref 带 ref_type 段）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            agent = f'a_{uuid4().hex[:16]}'
            res = await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report',
                owner_id=owner, op='create', op_id='op-c1', base_revision=None,
                local_ref='lr-1', server_id=None, fields=_report_fields(),
                agent_hasn_id=agent, title='贵州茅台投研',
            )
            sid = res['id']
            assert res['revision'] == 1 and res['op'] == 'create'
            uri = f'hasn://finance/reports/{sid}'

            row = (await db.execute(sa.select(ResearchReport).where(ResearchReport.id == int(sid)))).scalar_one()
            assert row.owner_id == owner and row.revision == 1
            assert row.last_client_op_id == 'op-c1' and row.status == 'active'

            arts = await _active_artifacts(db, owner=owner, uri=uri)
            assert len(arts) == 1
            art = arts[0]
            assert art.kind == 'resource'
            assert art.resource_kind == 'finance.research_report'
            assert art.origin_ref == f'resource:finance:research:{sid}'  # 多资源回指
            assert art.agent_hasn_id == agent and art.owner_hasn_id == owner
        finally:
            await db.rollback()


async def test_sync_create_idempotent_replay_same_local_ref() -> None:
    """create 幂等回放：同 (owner, local_ref) 重发 → 返回既有行、不重复铸、artifact 仍一条。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            agent = f'a_{uuid4().hex[:16]}'
            kw = dict(
                model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='create', base_revision=None, local_ref='lr-dup', server_id=None,
                fields=_report_fields(), agent_hasn_id=agent, title='X',
            )
            first = await finance_sync_service.sync_product(db, op_id='op-a', **kw)
            second = await finance_sync_service.sync_product(db, op_id='op-b', **kw)
            assert first['id'] == second['id']  # 同一行

            rows = (
                await db.execute(sa.select(ResearchReport).where(ResearchReport.local_ref == 'lr-dup'))
            ).scalars().all()
            assert len(rows) == 1  # 未重复铸
            arts = await _active_artifacts(db, owner=owner, uri=f"hasn://finance/reports/{first['id']}")
            assert len(arts) == 1
        finally:
            await db.rollback()


async def test_sync_update_bumps_revision_then_conflict_on_stale_base() -> None:
    """update → revision+1；随后用落后的 base_revision → 409 ConflictError（带服务端快照）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            agent = f'a_{uuid4().hex[:16]}'
            created = await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='create', op_id='op-1', base_revision=None, local_ref='lr-u', server_id=None,
                fields=_report_fields(), agent_hasn_id=agent, title='原标题',
            )
            sid = created['id']
            updated = await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='update', op_id='op-2', base_revision=1, local_ref=None, server_id=sid,
                fields={'title': '新标题'}, agent_hasn_id=agent, title='新标题',
            )
            assert updated['revision'] == 2
            row = (await db.execute(sa.select(ResearchReport).where(ResearchReport.id == int(sid)))).scalar_one()
            assert row.title == '新标题' and row.last_client_op_id == 'op-2'

            with pytest.raises(errors.ConflictError) as conflict:  # base_revision=1 落后于当前 2
                await finance_sync_service.sync_product(
                    db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                    op='update', op_id='op-3', base_revision=1, local_ref=None, server_id=sid,
                    fields={'title': '再改'}, agent_hasn_id=agent,
                )
            evidence = conflict.value.data
            assert evidence['conflict'] is True
            assert evidence['server_id'] == sid
            assert evidence['revision'] == 2
            snapshot = evidence['snapshot']
            assert snapshot['id'] == int(sid)
            assert snapshot['revision'] == 2
            assert snapshot['title'] == '新标题'
            assert snapshot['body_md'] == ''
            assert snapshot['findings_json'] == [{'point': '毛利率提升'}]
            assert snapshot['status'] == 'active'
            assert 'local_ref' not in snapshot
            assert 'last_client_op_id' not in snapshot
        finally:
            await db.rollback()


async def test_sync_delete_soft_deletes_all_active_pointers() -> None:
    """delete → 业务行 status=deleted + 该 owner/uri 全部 active artifact 指针软删（契约1 删除半场）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            agent = f'a_{uuid4().hex[:16]}'
            created = await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='create', op_id='op-1', base_revision=None, local_ref='lr-d', server_id=None,
                fields=_report_fields(), agent_hasn_id=agent, title='待删',
            )
            sid = created['id']
            uri = f'hasn://finance/reports/{sid}'
            assert len(await _active_artifacts(db, owner=owner, uri=uri)) == 1

            await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='delete', op_id='op-2', base_revision=1, local_ref=None, server_id=sid, fields={},
                agent_hasn_id=agent,
            )
            row = (await db.execute(sa.select(ResearchReport).where(ResearchReport.id == int(sid)))).scalar_one()
            assert row.status == 'deleted'
            assert len(await _active_artifacts(db, owner=owner, uri=uri)) == 0  # 指针全软删
        finally:
            await db.rollback()


async def test_sync_cross_owner_update_returns_404_zero_write() -> None:
    """伪造 owner：update 别人 owner 的 server_id → 404，且零写入（owner 隔离，契约1/7）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            other = await _seed_owner(db)
            created = await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='create', op_id='op-1', base_revision=None, local_ref='lr-x', server_id=None,
                fields=_report_fields(), agent_hasn_id=f'a_{uuid4().hex[:16]}', title='owner的',
            )
            sid = created['id']
            with pytest.raises(errors.NotFoundError):
                await finance_sync_service.sync_product(
                    db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=other,
                    op='update', op_id='op-2', base_revision=1, local_ref=None, server_id=sid,
                    fields={'title': '越权改'}, agent_hasn_id=f'a_{uuid4().hex[:16]}',
                )
            row = (await db.execute(sa.select(ResearchReport).where(ResearchReport.id == int(sid)))).scalar_one()
            # 业务 title 列取自 _report_fields()（'贵州茅台投研'）；sync_product 的 title= 入参是
            # 产物登记标题（另一码事）。越权 update 想写 '越权改' 被 404 拦下 → 业务行零改动。
            assert row.title == '贵州茅台投研' and row.revision == 1  # 未被越权修改
        finally:
            await db.rollback()


async def test_sync_owner_manual_writes_row_but_skips_registration() -> None:
    """主人手建（agent_hasn_id 空）：业务行照写，但跳过 register-on-write（判据「分身参与」）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            created = await finance_sync_service.sync_product(
                db, model_cls=ResearchReport, resource_kind='finance.research_report', owner_id=owner,
                op='create', op_id='op-1', base_revision=None, local_ref='lr-m', server_id=None,
                fields=_report_fields(), agent_hasn_id=None, title='主人手建',
            )
            sid = created['id']
            row = (await db.execute(sa.select(ResearchReport).where(ResearchReport.id == int(sid)))).scalar_one()
            assert row.status == 'active'  # 业务行照写
            arts = await _active_artifacts(db, owner=owner, uri=f'hasn://finance/reports/{sid}')
            assert len(arts) == 0  # 分身没碰 → 不登记
        finally:
            await db.rollback()


async def test_strategy_share_rejected_server_side() -> None:
    """契约5：finance.strategy 分享服务端硬拒（ForbiddenError），不能只藏 UI。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            with pytest.raises(errors.ForbiddenError):
                await ResourceShareService.upsert_share(
                    db, resource_type='finance.strategy', resource_id='1', owner_hasn_id=owner,
                    grantee_type='human', grantee_id='h_someone', permission='viewer', granted_by=owner,
                )
        finally:
            await db.rollback()
