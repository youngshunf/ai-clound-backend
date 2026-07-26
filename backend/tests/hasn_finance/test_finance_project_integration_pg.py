"""FIN-F2-5——金融投研接入平台项目的真实 PostgreSQL 契约。

覆盖可选项目档位、两类容器挂靠、owner 隔离、项目过滤、挂靠资源深链，以及
“直接打标产物 ∪ 挂靠容器名下历史产物”的并集读。所有写入在测试事务末尾回滚。
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_finance.manifest import FINANCE_AI_NATIVE_MANIFEST
from backend.app.hasn_finance.model.backtest_report import BacktestReport
from backend.app.hasn_finance.model.research_report import ResearchReport
from backend.app.hasn_finance.model.shadow_account import ShadowAccount
from backend.app.hasn_finance.model.strategy import Strategy
from backend.app.hasn_finance.model.trade_review import TradeReview
from backend.app.hasn_finance.model.watch_briefing import WatchBriefing
from backend.app.hasn_finance.service.finance_read_service import finance_read_service
from backend.app.hasn_finance.service.finance_sync_service import finance_sync_service
from backend.app.hasn_project.service.project_app_service import ProjectService
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def _seed_owner(db) -> str:  # noqa: ANN001
    """创建满足金融产物登记约束的主人。"""
    uid = 780_000_000 + (uuid4().int % 100_000_000)
    owner = f'h_{uuid4().hex[:16]}'
    db.add(HasnHumans(hasn_id=owner, user_id=uid, star_id=str(uid), nickname=owner, status='active'))
    await db.flush()
    return owner


async def _sync(
    db,  # noqa: ANN001
    *,
    owner: str,
    model: type,
    kind: str,
    fields: dict,
    local_ref: str,
    agent: str | None = None,
) -> str:
    """经真实 :sync 核心创建金融资源并返回云端权威 id。"""
    result = await finance_sync_service.sync_product(
        db,
        model_cls=model,
        resource_kind=kind,
        owner_id=owner,
        op='create',
        op_id=f'op-{uuid4().hex[:12]}',
        base_revision=None,
        local_ref=local_ref,
        server_id=None,
        fields=fields,
        agent_hasn_id=agent,
        title=str(fields.get('title') or fields.get('name') or kind),
    )
    return result['id']


async def test_finance_project_contract_has_exact_boundaries() -> None:
    """manifest、descriptor、adapter 与禁止挂靠对象保持精确边界。"""
    assert FINANCE_AI_NATIVE_MANIFEST['project_aware'] is True
    assert FINANCE_AI_NATIVE_MANIFEST['project_required'] is False

    resources = {
        item['resource_kind']: item['uri_domain']
        for item in FINANCE_AI_NATIVE_MANIFEST['resources']
    }
    expected = {
        'finance.strategy': 'finance/strategies',
        'finance.shadow_account': 'finance/shadow',
    }
    for kind, domain in expected.items():
        adapter = project_linkage_registry.get(domain)
        assert adapter is not None
        assert adapter.domain == resources[kind]
        assert adapter.app_id == 'finance'
        assert adapter.is_container is True

    registered = {adapter.domain for adapter in project_linkage_registry.container_adapters()}
    assert 'finance/watchlist' not in registered
    assert 'finance/privacy' not in registered
    assert 'finance/engine' not in registered

    # 四类纯报告只经 hasn_artifacts.project_id 归集，业务表不重复存 project_id。
    for model in (ResearchReport, BacktestReport, TradeReview, WatchBriefing):
        assert 'project_id' not in {column.name for column in model.__table__.columns}


async def test_strategy_and_shadow_link_reassign_unlink_filter_and_owner_isolation() -> None:
    """两容器统一经注册表挂靠、改挂、摘除，并支持 owner 隔离的项目过滤。"""
    service = ProjectService()
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            other = await _seed_owner(db)
            project_a = await service.create_project(db, owner=owner, data={'name': '项目 A'})
            project_b = await service.create_project(db, owner=owner, data={'name': '项目 B'})
            other_project = await service.create_project(db, owner=other, data={'name': '他人项目'})

            strategy_id = await _sync(
                db,
                owner=owner,
                model=Strategy,
                kind='finance.strategy',
                local_ref=f'fstg-{uuid4().hex[:10]}',
                fields={'name': '低波策略', 'market': 'cn', 'source': 'manual'},
            )
            shadow_id = await _sync(
                db,
                owner=owner,
                model=ShadowAccount,
                kind='finance.shadow_account',
                local_ref=f'fsha-{uuid4().hex[:10]}',
                fields={'account_alias': '稳健账户', 'version': 1},
            )

            strategy_uri = f'hasn://finance/strategies/{strategy_id}'
            shadow_uri = f'hasn://finance/shadow/{shadow_id}'
            linked = await project_linkage_registry.link(
                db,
                owner=owner,
                resource_uri=strategy_uri,
                project_id=project_a['id'],
            )
            assert linked['changed'] is True
            assert (
                await db.execute(sa.select(Strategy.revision).where(Strategy.id == int(strategy_id)))
            ).scalar_one() == 2
            await project_linkage_registry.link(
                db,
                owner=owner,
                resource_uri=shadow_uri,
                project_id=project_a['id'],
            )

            filtered = await finance_read_service.list_resources(
                db,
                resource_kind='finance.strategy',
                owner_id=owner,
                filters={'platform_project_id': project_a['id']},
            )
            assert [str(item['id']) for item in filtered['items']] == [strategy_id]

            detail = await service.get_project(db, owner=owner, pk=project_a['id'])
            linked_by_uri = {item['resource_uri']: item for item in detail['linked_resources']}
            assert linked_by_uri[strategy_uri]['title'] == '低波策略'
            assert linked_by_uri[shadow_uri]['title'] == '稳健账户'
            assert all(item['app_id'] == 'finance' for item in linked_by_uri.values())

            reassigned = await project_linkage_registry.link(
                db,
                owner=owner,
                resource_uri=strategy_uri,
                project_id=project_b['id'],
            )
            assert reassigned['changed'] is True
            assert reassigned['previous_project_id'] == project_a['id']
            assert (
                await db.execute(sa.select(Strategy.revision).where(Strategy.id == int(strategy_id)))
            ).scalar_one() == 3
            with pytest.raises(errors.ConflictError):
                await project_linkage_registry.unlink(
                    db,
                    owner=owner,
                    resource_uri=strategy_uri,
                    project_id=project_a['id'],
                )
            assert (
                await db.execute(sa.select(Strategy.revision).where(Strategy.id == int(strategy_id)))
            ).scalar_one() == 3
            old_filtered = await finance_read_service.list_resources(
                db,
                resource_kind='finance.strategy',
                owner_id=owner,
                filters={'platform_project_id': project_a['id']},
            )
            new_filtered = await finance_read_service.list_resources(
                db,
                resource_kind='finance.strategy',
                owner_id=owner,
                filters={'platform_project_id': project_b['id']},
            )
            assert old_filtered['items'] == []
            assert [str(item['id']) for item in new_filtered['items']] == [strategy_id]

            unlinked = await project_linkage_registry.unlink(
                db,
                owner=owner,
                resource_uri=strategy_uri,
                project_id=project_b['id'],
            )
            assert unlinked['changed'] is True
            assert unlinked['previous_project_id'] == project_b['id']
            assert (
                await db.execute(sa.select(Strategy.revision).where(Strategy.id == int(strategy_id)))
            ).scalar_one() == 4
            replayed = await project_linkage_registry.unlink(
                db,
                owner=owner,
                resource_uri=strategy_uri,
                project_id=project_b['id'],
            )
            assert replayed['changed'] is False
            assert (
                await db.execute(sa.select(Strategy.revision).where(Strategy.id == int(strategy_id)))
            ).scalar_one() == 4

            with pytest.raises(errors.NotFoundError):
                await project_linkage_registry.link(
                    db,
                    owner=other,
                    resource_uri=strategy_uri,
                    project_id=other_project['id'],
                )
        finally:
            await db.rollback()


async def test_project_artifact_flow_includes_finance_container_descendants_only() -> None:
    """挂靠策略/影子账户后，其历史回测/复盘并入项目，未归属产物不混入。"""
    service = ProjectService()
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            agent = f'a_{uuid4().hex[:16]}'
            project = await service.create_project(db, owner=owner, data={'name': '历史归集'})

            strategy_id = await _sync(
                db,
                owner=owner,
                model=Strategy,
                kind='finance.strategy',
                local_ref=f'fstg-{uuid4().hex[:10]}',
                fields={'name': '红利策略', 'market': 'cn', 'source': 'swarm'},
                agent=agent,
            )
            backtest_id = await _sync(
                db,
                owner=owner,
                model=BacktestReport,
                kind='finance.backtest_report',
                local_ref=f'fbtr-{uuid4().hex[:10]}',
                fields={
                    'strategy_id': int(strategy_id),
                    'title': '红利策略回测',
                    'period_start': date(2025, 1, 1),
                    'period_end': date(2025, 12, 31),
                    'engine_version': '2.0.0',
                    'data_source': 'akshare',
                },
                agent=agent,
            )
            unrelated_backtest_id = await _sync(
                db,
                owner=owner,
                model=BacktestReport,
                kind='finance.backtest_report',
                local_ref=f'fbtr-{uuid4().hex[:10]}',
                fields={
                    'title': '临时试跑',
                    'period_start': date(2025, 1, 1),
                    'period_end': date(2025, 12, 31),
                    'engine_version': '2.0.0',
                    'data_source': 'akshare',
                },
                agent=agent,
            )
            shadow_id = await _sync(
                db,
                owner=owner,
                model=ShadowAccount,
                kind='finance.shadow_account',
                local_ref=f'fsha-{uuid4().hex[:10]}',
                fields={'account_alias': '复盘账户', 'version': 1},
                agent=agent,
            )
            review_id = await _sync(
                db,
                owner=owner,
                model=TradeReview,
                kind='finance.trade_review',
                local_ref=f'ftrv-{uuid4().hex[:10]}',
                fields={
                    'shadow_account_id': int(shadow_id),
                    'title': '季度复盘',
                    'body_md': '# 复盘',
                    'shadow_backtest_id': int(unrelated_backtest_id),
                },
                agent=agent,
            )
            truly_unrelated_backtest_id = await _sync(
                db,
                owner=owner,
                model=BacktestReport,
                kind='finance.backtest_report',
                local_ref=f'fbtr-{uuid4().hex[:10]}',
                fields={
                    'title': '无容器临时试跑',
                    'period_start': date(2025, 1, 1),
                    'period_end': date(2025, 12, 31),
                    'engine_version': '2.0.0',
                    'data_source': 'akshare',
                },
                agent=agent,
            )

            await project_linkage_registry.link(
                db,
                owner=owner,
                resource_uri=f'hasn://finance/strategies/{strategy_id}',
                project_id=project['id'],
            )
            await project_linkage_registry.link(
                db,
                owner=owner,
                resource_uri=f'hasn://finance/shadow/{shadow_id}',
                project_id=project['id'],
            )

            flow = await service.project_artifact_flow(db, owner=owner, project_id=project['id'])
            uris = {item['resource_uri'] for item in flow['items']}
            assert f'hasn://finance/strategies/{strategy_id}' in uris
            assert f'hasn://finance/backtests/{backtest_id}' in uris
            assert f'hasn://finance/shadow/{shadow_id}' in uris
            assert f'hasn://finance/reviews/{review_id}' in uris
            assert f'hasn://finance/backtests/{unrelated_backtest_id}' in uris
            assert f'hasn://finance/backtests/{truly_unrelated_backtest_id}' not in uris
            assert flow['total'] == len(flow['items'])
            assert all(
                item['project_relation'] == {
                    'project_id': project['id'],
                    'via': 'linked_container',
                }
                for item in flow['items']
            )

            await project_linkage_registry.unlink(
                db,
                owner=owner,
                resource_uri=f'hasn://finance/strategies/{strategy_id}',
                project_id=project['id'],
            )
            await project_linkage_registry.unlink(
                db,
                owner=owner,
                resource_uri=f'hasn://finance/shadow/{shadow_id}',
                project_id=project['id'],
            )
            detached = await service.project_artifact_flow(db, owner=owner, project_id=project['id'])
            assert detached['items'] == []
            assert detached['total'] == 0
        finally:
            await db.rollback()
