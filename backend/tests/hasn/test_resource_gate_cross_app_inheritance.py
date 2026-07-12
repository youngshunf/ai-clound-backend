"""G6 统一资源权限门·跨应用继承语义参数化守卫（doc33 S3-4·零 mock）。

把 S1-4「knowledge 三场景 RED 守卫」的**继承语义三场景**参数化为 `@pytest.mark.parametrize`
铺到**全部已接入应用**（每应用给 seed/id 参映射），一份测试锁死统一门在各应用上的一致行为：

  场景①  owner 的分身过门（owner_grant）→ manager >= viewer/editor，委托 owner key = 资源主人；
  场景②  owner 显式 share viewer 给 B → B 的分身可读（viewer 过门）不可写（editor → 403 档位不足）；
  场景③  撤销后 / 从未获授 → 404（存在性隐藏，绝不泄露 403 或冒 500）。

**为什么还要这份**（与各应用 `test_<app>_resource_gate.py` 的关系）：per-app 测试逐应用深覆盖（含
builtin/可选参/父链等应用特有通路）；本文件是**对偶的横向守卫**——用同一份断言铺全量已接入应用，
保证「新接一个应用就把它加进 `_SPECS`」这一步能立刻暴露该应用是否偏离统一继承语义（新应用漏跑三场景
= 红）。二者互补：per-app 保深度，本文件保「全量一致、不漂移」。

统一驱动：各应用的 share **一律经平台泛型 `ResourceShareService.upsert_share`**（studio_service /
design_system_service / knowledge_service 的 add_*_share 最终都收敛到它，见 studio_service §122/§137），
故 share/revoke 是**跨应用统一**的；唯一按应用不同的是**建 owner 资源行的 seed**（`_SPECS` 每项给一个）。
门经 `resolve_effective_permission` 内核读 `hasn_resource_share`（语义不动）。事务 flush 不 commit、末尾
rollback，不污染库。需要：export DATABASE_PORT=15432。

design（矢量设计）是**分享登记表推导 owner**的特例（无「按 id 查资源行」的形状，owner 从 share 行反推），
不套本文件「先建行、再分享」的统一 seed 形状，其三场景由专属 `test_design_resource_gate.py` 深覆盖。
"""

from __future__ import annotations

import uuid

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# import 即注册：保证 registered_types() 在门求值前已含各已接入应用的资源类型。
import backend.app.hasn_deck.service.resource_adapter  # noqa: F401
import backend.app.hasn_designsystem.service.resource_adapter  # noqa: F401
import backend.app.hasn_knowledge.service.resource_adapter  # noqa: F401
import backend.app.hasn_plan.service.resource_adapter  # noqa: F401
import backend.app.hasn_studio.service.resource_adapter  # noqa: F401

from backend.app.hasn.service.authz.resource_gate import enforce_declaration
from backend.app.hasn.service.authz.subject import Subject
from backend.app.hasn.service.resource_share_service import ResourceShareService
from backend.app.hasn_deck.model import Deck
from backend.app.hasn_designsystem.service.design_system_service import design_system_service
from backend.app.hasn_knowledge.model import Kb
from backend.app.hasn_plan.model import Event
from backend.app.hasn_studio.model import StudioProject
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


# ── 各应用建 owner 资源行的 seed（复用各 per-app 测试证明可用的最小字段集，返回云端权威 id）───────
async def _seed_knowledge(session: AsyncSession, owner: str, tag: str) -> int:
    kb = Kb(
        owner_id=owner,
        scope='personal',
        enterprise_id=None,
        name=f'库-{tag}',
        description=None,
        ragflow_dataset_id=f'rf_{uuid.uuid4().hex[:12]}',
        embedding_model='bge',
        document_count=0,
        chunk_count=0,
        status='active',
        visibility='private',
    )
    session.add(kb)
    await session.flush()
    return kb.id


async def _seed_deck(session: AsyncSession, owner: str, tag: str) -> int:
    deck = Deck(owner_id=owner, title=f'deck-{tag}', status='draft', language='zh', source='agent', rev=1)
    session.add(deck)
    await session.flush()
    return deck.id


async def _seed_studio(session: AsyncSession, owner: str, tag: str) -> int:
    proj = StudioProject(
        owner_hasn_id=owner,
        title=f'项目-{tag}',
        description=None,
        default_pipeline_key='cinematic',
        settings={},
        status='draft',
    )
    session.add(proj)
    await session.flush()
    return proj.id


async def _seed_plan_event(session: AsyncSession, owner: str, tag: str) -> int:
    ev = Event(
        owner_hasn_id=owner, enterprise_id=None, title=f'事件-{tag}', start_at=_T0, end_at=_T1, visibility='private'
    )
    session.add(ev)
    await session.flush()
    return int(ev.id)


def _ds_content() -> dict:
    """designsystem save 组版所需的最小内容（每次新建，避免可变默认共享）。"""
    return {
        'tokens_css': ':root { --bg: #101010; }',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 说明',
        'components_html': '<button>Go</button>',
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'summary': {'score': 70, 'grade': 'fair', 'recommendRebuild': False}},
    }


async def _seed_designsystem(session: AsyncSession, owner: str, tag: str) -> int:
    saved = await design_system_service.save(
        session,
        subject=Subject.human(owner),
        design_system_id=None,
        slug=f'ds-{tag}',
        name=f'设计系统-{tag}',
        content=_ds_content(),
        enterprise_id=None,
    )
    return int(saved['id'])


@dataclass(frozen=True)
class AppGateSpec:
    """一个已接入应用的门测试规约：显示名 + 入参名 + resource_type + 建 owner 资源行的 seed。"""

    label: str
    id_param: str
    resource_type: str
    seed: Callable[[AsyncSession, str, str], Awaitable[int]]


# 已接入 G6 且套「先建行、再经泛型 share」统一形状的应用全量（design 特例见模块 docstring，走专属测试）。
_SPECS: list[AppGateSpec] = [
    AppGateSpec('knowledge', 'kb_id', 'knowledge', _seed_knowledge),
    AppGateSpec('deck', 'deck_id', 'deck', _seed_deck),
    AppGateSpec('designsystem', 'design_system_id', 'designsystem', _seed_designsystem),
    AppGateSpec('studio_project', 'project_id', 'studio_project', _seed_studio),
    AppGateSpec('plan_event', 'event_id', 'plan_event', _seed_plan_event),
]
_IDS = [s.label for s in _SPECS]


# ── 跨应用统一的 share / revoke（一律经平台泛型 resource_share，不走应用 add_*_share 包装）─────────
async def _share(
    session: AsyncSession,
    spec: AppGateSpec,
    resource_id: int,
    *,
    owner: str,
    grantee_type: str,
    grantee_id: str,
    permission: str,
) -> None:
    await ResourceShareService.upsert_share(
        session,
        resource_type=spec.resource_type,
        resource_id=str(resource_id),
        owner_hasn_id=owner,
        grantee_type=grantee_type,
        grantee_id=grantee_id,
        permission=permission,
        granted_by=owner,
    )


async def _revoke(
    session: AsyncSession, spec: AppGateSpec, resource_id: int, *, grantee_type: str, grantee_id: str
) -> None:
    await ResourceShareService.revoke_share(
        session,
        resource_type=spec.resource_type,
        resource_id=str(resource_id),
        grantee_type=grantee_type,
        grantee_id=grantee_id,
    )


def _decl(spec: AppGateSpec, need: str) -> list[dict]:
    return [{'param': spec.id_param, 'type': spec.resource_type, 'need': need}]


@pytest.mark.parametrize('spec', _SPECS, ids=_IDS)
async def test_owner_agent_gets_manager(session: AsyncSession, spec: AppGateSpec) -> None:
    """场景①：owner A 的分身过门（owner_grant）→ manager >= viewer/editor，委托 owner key = A（不需任何 share）。"""
    tag = uuid.uuid4().hex[:8]
    a = f'h_a_{tag}'
    a_agent = Subject.agent(f'a_a_{tag}', a)
    rid = await spec.seed(session, a, tag)

    for need in ('viewer', 'editor'):
        out = await enforce_declaration(session, a_agent, _decl(spec, need), {spec.id_param: rid})
        got = out[spec.id_param]
        assert got.owner_hasn_id == a, f'{spec.label}: owner_grant 委托键应为资源主人 A'
        assert got.permission == 'manager', f'{spec.label}: owner 应得 manager'


@pytest.mark.parametrize('spec', _SPECS, ids=_IDS)
async def test_shared_viewer_reads_ok_writes_forbidden(session: AsyncSession, spec: AppGateSpec) -> None:
    """场景②：A 显式 share viewer 给 B → B 的分身可读（viewer 过门）不可写（editor → 403 档位不足，非 404）。"""
    tag = uuid.uuid4().hex[:8]
    a = f'h_a_{tag}'
    b = f'h_b_{tag}'
    b_agent = Subject.agent(f'a_b_{tag}', b)
    rid = await spec.seed(session, a, tag)
    await _share(session, spec, rid, owner=a, grantee_type='human', grantee_id=b, permission='viewer')

    ok = await enforce_declaration(session, b_agent, _decl(spec, 'viewer'), {spec.id_param: rid})
    got = ok[spec.id_param]
    assert got.owner_hasn_id == a, f'{spec.label}: 委托键应为资源主人 A（非发起分身主人 B）'
    assert got.permission == 'viewer', f'{spec.label}: B 应得 viewer'
    with pytest.raises(errors.ForbiddenError):  # 有权但档位不足 → 403，绝不降级为 404
        await enforce_declaration(session, b_agent, _decl(spec, 'editor'), {spec.id_param: rid})


@pytest.mark.parametrize('spec', _SPECS, ids=_IDS)
async def test_revoke_and_never_shared_are_not_found(session: AsyncSession, spec: AppGateSpec) -> None:
    """场景③：撤销后 / 从未获授 → 404（存在性隐藏，绝不泄露 403 或冒 500）。"""
    tag = uuid.uuid4().hex[:8]
    a = f'h_a_{tag}'
    b = f'h_b_{tag}'
    b_agent = Subject.agent(f'a_b_{tag}', b)
    d_agent = Subject.agent(f'a_d_{tag}', f'h_d_{tag}')  # 从未获授的第三方分身
    rid = await spec.seed(session, a, tag)
    await _share(session, spec, rid, owner=a, grantee_type='human', grantee_id=b, permission='viewer')

    # 撤销前 B 能读
    await enforce_declaration(session, b_agent, _decl(spec, 'viewer'), {spec.id_param: rid})
    await _revoke(session, spec, rid, grantee_type='human', grantee_id=b)

    # 撤销后 B 失权 → 404（资源仍在但对 B 不可见，存在性隐藏）
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, b_agent, _decl(spec, 'viewer'), {spec.id_param: rid})
    # 从未获授的第三方分身 → 404
    with pytest.raises(errors.NotFoundError):
        await enforce_declaration(session, d_agent, _decl(spec, 'viewer'), {spec.id_param: rid})
