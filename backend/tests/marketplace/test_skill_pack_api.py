"""技能包 skill_pack 路由 进程内 HTTP E2E（实施/91 B2.3，真实 PG，零 mock）。

覆盖 B2 收编后的契约：
  1) POST 创建合法 skill_pack → 真落 marketplace_template(skill_pack) + version
     (bundle_slug/command_key/hermes_yaml)，hermes_yaml 是 safe_dump 规范化产出。
  2) 非法 hermes_yaml（空 skills / 顶层非 dict / slug 不自洽）→ 400 RequestError。
  3) GET 列表返回本作者私有包，他人私有不可见。

最小 app 挂真实 skill_pack 路由 + 真实 PG + override JWT（注入 request.scope['user']）；经
ASGITransport 走完整 HTTP。事务末尾回滚不污染库。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import yaml

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.api.v1.skill_pack import router as skill_pack_router
from backend.app.marketplace.model import MarketplaceSkill
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_AUTHOR_ID = 970077
# 技能包默认成员（_payload 的 hermes_yaml 引用这两个完整 id，故种子必须用这两个固定值）。
# 它们以「裸分类名/slug」存在，是本测试专用桩；teardown 按 id 兜底删除，禁止泄漏进共享本地库。
_MEMBER_SKILL_IDS = ['developer/code-review', 'productivity/tdd']

_APP = FastAPI()
_APP.include_router(skill_pack_router, prefix='/api/v1/marketplace/app/skill-packs')


@_APP.exception_handler(BaseExceptionError)
async def _err_handler(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={'code': exc.code, 'msg': str(exc.msg), 'data': None})


class _InjectUser:
    """纯 ASGI 包装：为所有请求注入 scope['user']（list 路由无鉴权依赖，靠全局中间件填 user，
    生产由 JWT 中间件设置；测试里用它替代，避免 BaseHTTPMiddleware 的跨事件循环坑）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get('type') == 'http':
            scope['user'] = SimpleNamespace(id=_AUTHOR_ID)
        await self.app(scope, receive, send)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _seed_skill(session, namespace: str, slug: str, **cols) -> str:
    """落一条已发布公开技能（实施/92：技能包成员必须是已发布公开技能才能解析）。"""
    skill_id = f'{namespace}/{slug}'
    await session.execute(delete(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id))
    session.add(
        MarketplaceSkill(
            skill_id=skill_id,
            namespace=namespace,
            slug=slug,
            name=cols.pop('name', slug),
            status='published',
            visibility='public',
            **cols,
        )
    )
    await session.flush()
    return skill_id


async def _seed_default_members(session) -> None:
    """_payload 默认成员（developer/code-review + productivity/tdd）落库为已发布公开技能。"""
    for skill_id in _MEMBER_SKILL_IDS:
        namespace, slug = skill_id.rsplit('/', 1)
        await _seed_skill(session, namespace, slug)


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    async def _yield_session():
        yield session

    async def _auth(request: Request) -> None:
        request.scope['user'] = SimpleNamespace(id=_AUTHOR_ID)

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=_InjectUser(_APP)), base_url='http://e2e')
    try:
        yield SimpleNamespace(http=http, session=session)
    finally:
        await http.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        # 显式硬清理（兜底）：成员桩与作者包在「真实 :8020 commit / 中途提交 / 用例中断」时
        # rollback 盖不住，会以「无名无图无描述」破卡片污染共享本地库的市场浏览。按本测试已知
        # 作者 id + 固定成员 id 删除，幂等且只命中测试自造数据，不触碰真实 huanxing/* 行。
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    'DELETE FROM hasn_marketplace.marketplace_template_version WHERE template_id IN '
                    '(SELECT template_id FROM hasn_marketplace.marketplace_template WHERE author_id = ANY(:authors))'
                ),
                {'authors': [_AUTHOR_ID, _AUTHOR_ID + 1]},
            )
            await conn.execute(
                text('DELETE FROM hasn_marketplace.marketplace_template WHERE author_id = ANY(:authors)'),
                {'authors': [_AUTHOR_ID, _AUTHOR_ID + 1]},
            )
            await conn.execute(
                text('DELETE FROM hasn_marketplace.marketplace_skill WHERE skill_id = ANY(:ids)'),
                {'ids': _MEMBER_SKILL_IDS},
            )
        await engine.dispose()


def _payload(slug: str, **over) -> dict:
    base = {
        'namespace': 'huanxing',
        'name': slug,
        'description': 'Backend tools',
        'bundle_slug': slug,
        'command_key': f'/{slug}',
        'version': '1.0.0',
        'hermes_bundle_json': {'skills': ['developer/code-review']},
        'hermes_yaml': f'name: {slug}\ndescription: 后端开发\nskills:\n  - developer/code-review\n  - productivity/tdd\n',
        'is_private': False,
        'is_official': True,
    }
    base.update(over)
    return base


async def test_create_valid_skill_pack_persists_normalized_contract(client) -> None:
    await _seed_default_members(client.session)
    slug = f'backend-dev-{_tag()}'
    r = await client.http.post('/api/v1/marketplace/app/skill-packs', json=_payload(slug))
    assert r.status_code == 200, r.text
    data = r.json()['data']
    assert data['template_id'] == f'huanxing/{slug}'
    assert data['command_key'] == f'/{slug}'
    # hermes_yaml 是 safe_dump 规范化产出（含两个成员）
    assert 'developer/code-review' in data['hermes_yaml']
    assert 'productivity/tdd' in data['hermes_yaml']

    row = (
        await client.session.execute(
            text(
                """
                SELECT t.template_type, v.bundle_slug, v.command_key, v.hermes_yaml, v.content_hash
                FROM hasn_marketplace.marketplace_template t
                JOIN hasn_marketplace.marketplace_template_version v ON v.template_id = t.template_id AND v.is_latest
                WHERE t.template_id = :tid
                """
            ),
            {'tid': f'huanxing/{slug}'},
        )
    ).mappings().one()
    assert row['template_type'] == 'skill_pack'
    assert row['bundle_slug'] == slug
    assert row['command_key'] == f'/{slug}'
    assert row['content_hash'].startswith('sha256:')


async def test_create_rejects_invalid_hermes_yaml(client) -> None:
    slug = f'bad-{_tag()}'
    # 空 skills
    r = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, hermes_yaml=f'name: {slug}\nskills: []\n'),
    )
    assert r.status_code == 400, r.text
    # slug 不自洽（command_key 不是 /slug）
    r2 = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, command_key='/wrong'),
    )
    assert r2.status_code == 400, r2.text
    # 顶层非 dict
    r3 = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, hermes_yaml='- just\n- a\n- list\n'),
    )
    assert r3.status_code == 400, r3.text


async def test_list_hides_other_owner_private_packs(client) -> None:
    await _seed_default_members(client.session)
    tag = _tag()
    slug = f'priv-{tag}'
    # 当前作者的私有包
    await client.http.post('/api/v1/marketplace/app/skill-packs', json=_payload(slug, is_private=True, is_official=False))
    # 他人的私有包（直接落库，author_id 不同）
    other_slug = f'other-{tag}'
    await client.session.execute(
        text(
            """
            INSERT INTO hasn_marketplace.marketplace_template (template_id, namespace, slug, template_type, name,
                author_id, pricing_type, price, is_private, is_official, download_count, source_type,
                created_time, updated_time)
            VALUES (:tid, 'huanxing', :slug, 'skill_pack', :slug, :other, 'free', 0, true, false, 0, 'local', now(), now())
            """
        ),
        {'tid': f'huanxing/{other_slug}', 'slug': other_slug, 'other': _AUTHOR_ID + 1},
    )
    await client.session.execute(
        text(
            """
            INSERT INTO hasn_marketplace.marketplace_template_version (template_id, version, bundle_slug, command_key,
                hermes_yaml, content_hash, file_hash, is_latest, published_at, created_time, updated_time)
            VALUES (:tid, '1.0.0', :slug, :cmd, 'name: x\nskills:\n  - a\n', 'sha256:x', 'sha256:x', true, now(), now(), now())
            """
        ),
        {'tid': f'huanxing/{other_slug}', 'slug': other_slug, 'cmd': f'/{other_slug}'},
    )
    await client.session.flush()

    # 共享开发库中的官方技能包可能超过默认 24 条；按本用例的目标 slug 检索，
    # 避免把分页排序误判成私有可见性失败。
    r = await client.http.get('/api/v1/marketplace/app/skill-packs', params={'q': tag})
    assert r.status_code == 200, r.text
    slugs = [item['bundle_slug'] for item in r.json()['data']['items']]
    assert slug in slugs
    assert other_slug not in slugs  # 他人私有不可见


async def test_create_resolves_bare_slug_member_to_full_id(client) -> None:
    """实施/92 D-NAMING：成员可用裸 slug 提交，落库时归一为完整 namespace/slug id。"""
    member_slug = f'paper-digest-{_tag()}'
    await _seed_skill(client.session, 'huanxing/research', member_slug)
    slug = f'research-pack-{_tag()}'
    r = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(
            slug,
            hermes_bundle_json={'skills': [member_slug]},
            hermes_yaml=f'name: {slug}\nskills:\n  - {member_slug}\n',
        ),
    )
    assert r.status_code == 200, r.text
    data = r.json()['data']
    # 裸 slug 已归一为完整 id（落库 hermes_yaml 是权威），裸形态不再单独成行
    assert f'huanxing/research/{member_slug}' in data['hermes_yaml']
    spec = yaml.safe_load(data['hermes_yaml'])
    assert spec['skills'] == [f'huanxing/research/{member_slug}']


async def test_create_rejects_unpublished_member(client) -> None:
    """成员不是已发布公开技能 → 400（堵 gap#5，禁止打包未发布技能）。"""
    slug = f'ghost-pack-{_tag()}'
    ghost = f'ghostns/ghost-{_tag()}'
    r = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(
            slug,
            hermes_bundle_json={'skills': [ghost]},
            hermes_yaml=f'name: {slug}\nskills:\n  - {ghost}\n',
        ),
    )
    assert r.status_code == 400, r.text


async def test_create_rejects_ambiguous_bare_slug(client) -> None:
    """裸 slug 命中多个命名空间 → 400，要求用完整 namespace/slug 消歧。"""
    dup_slug = f'dup-{_tag()}'
    await _seed_skill(client.session, 'huanxing/teamA', dup_slug)
    await _seed_skill(client.session, 'huanxing/teamB', dup_slug)
    slug = f'dup-pack-{_tag()}'
    r = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(
            slug,
            hermes_bundle_json={'skills': [dup_slug]},
            hermes_yaml=f'name: {slug}\nskills:\n  - {dup_slug}\n',
        ),
    )
    assert r.status_code == 400, r.text


# ───────────────────────── 实施/92-UI：分类 + 草稿态 + 我的发布 ─────────────────────────


async def test_create_with_category_and_draft_status(client) -> None:
    """webui 创建：带分类 + status='draft' → 落库 category + status='draft' + user_id（进我的发布）。"""
    await _seed_default_members(client.session)
    slug = f'cat-{_tag()}'
    r = await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, category='development', status='draft', is_private=True, is_official=False),
    )
    assert r.status_code == 200, r.text
    row = (
        await client.session.execute(
            text(
                'SELECT category, status, user_id, author_id FROM hasn_marketplace.marketplace_template WHERE template_id = :tid'
            ),
            {'tid': f'huanxing/{slug}'},
        )
    ).mappings().one()
    assert row['category'] == 'development'
    assert row['status'] == 'draft'
    assert row['user_id'] == _AUTHOR_ID
    assert row['author_id'] == _AUTHOR_ID


async def test_draft_hidden_in_browse_but_shown_in_mine(client) -> None:
    """草稿包：市场浏览（默认）不可见，mine=true 可见（我的发布）。"""
    await _seed_default_members(client.session)
    slug = f'draft-{_tag()}'
    await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, status='draft', is_private=True, is_official=False),
    )
    browse = await client.http.get('/api/v1/marketplace/app/skill-packs')
    assert browse.status_code == 200, browse.text
    assert slug not in [it['bundle_slug'] for it in browse.json()['data']['items']]

    mine = await client.http.get('/api/v1/marketplace/app/skill-packs', params={'mine': 'true'})
    assert mine.status_code == 200, mine.text
    mine_items = {it['bundle_slug']: it for it in mine.json()['data']['items']}
    assert slug in mine_items
    assert mine_items[slug]['status'] == 'draft'


async def test_browse_filters_by_category(client) -> None:
    """分类筛选：?category=X 只返回该分类的已发布包。"""
    await _seed_default_members(client.session)
    a = f'wa-{_tag()}'
    b = f'wb-{_tag()}'
    await client.http.post('/api/v1/marketplace/app/skill-packs', json=_payload(a, category='writing'))
    await client.http.post('/api/v1/marketplace/app/skill-packs', json=_payload(b, category='research'))
    r = await client.http.get('/api/v1/marketplace/app/skill-packs', params={'category': 'writing'})
    assert r.status_code == 200, r.text
    slugs = [it['bundle_slug'] for it in r.json()['data']['items']]
    assert a in slugs
    assert b not in slugs
    # 卡片字段随出参返回（供 ResourceCard 渲染）
    card = next(it for it in r.json()['data']['items'] if it['bundle_slug'] == a)
    assert card['category'] == 'writing'
    assert card['namespace'] == 'huanxing'


async def test_publish_action_transitions_draft(client) -> None:
    """我的发布：草稿包 POST /{id}/publish → 进入待审（与模板发布工作流同构）。"""
    await _seed_default_members(client.session)
    slug = f'pub-{_tag()}'
    await client.http.post(
        '/api/v1/marketplace/app/skill-packs',
        json=_payload(slug, status='draft', is_private=True, is_official=False),
    )
    r = await client.http.post(f'/api/v1/marketplace/app/skill-packs/huanxing/{slug}/publish')
    assert r.status_code == 200, r.text
    assert r.json()['data']['status'] == 'pending_review'
