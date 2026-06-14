"""个人技能库 service 契约测试（SKILLSYNC-C2）。

三组覆盖：
1. 纯函数：`_extract_body_and_files` —— SKILL.md 正文去 frontmatter + 文件清单（恒跑，无外部依赖）。
2. 真实联调（真 PG + 真 S3 私有桶，七牛云不可达则 skip）：`upsert_from_package` 落表 + 传包 →
   `list_mine`/`get_mine` 可见 → 幂等去重（content_hash 未变不 bump 版本）→ 内容变更 bump 版本 →
   `get_package_for_owner` 下回字节往返一致。零 mock。
3. 业务语义（fake session + 注入 gateway，沿用 test_agent_skill_attach 既有惯例）：
   `attach_personal_skill_cloud_first` 并入 hasn_agents.skills、bump revision、发同步事件，
   含幂等 / owner 不归属 / Agent 404 / 个人技能 404 分支。

需要 export DATABASE_PORT=15432（本地 PG）。事务末尾回滚不污染库；S3 对象用 uuid 后缀 slug 隔离。
"""

from __future__ import annotations

import uuid
import zipfile

from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.service.marketplace_personal_skill_service import (
    _extract_body_and_files,
    personal_skill_service,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- helpers


def _make_skill_zip(*, name: str, description: str, body_note: str = '', slug: str | None = None) -> bytes:
    """构造一个最小合法技能 zip（SKILL.md frontmatter + 一个 references 资源文件）。"""
    front = f'---\nname: {name}\ndescription: {description}\n'
    if slug:
        front += f'slug: {slug}\n'
    front += 'version: 1.0.0\ncategory: productivity\ntags:\n  - test\n---\n'
    skill_md = front + f'# {name}\n\n{description}\n\n{body_note}'
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('SKILL.md', skill_md)
        zf.writestr('references/notes.md', f'# 资源\n\n{name} 的参考资料。\n')
    return buf.getvalue()


# --------------------------------------------------------------------------- 1. 纯函数


def test_extract_body_and_files_strips_frontmatter_and_lists_files() -> None:
    content = _make_skill_zip(name='笔记助手', description='帮你整理笔记', body_note='正文段落。')
    body, files_json = _extract_body_and_files(content)

    # 正文去掉了 YAML frontmatter，保留标题与段落。
    assert body is not None
    assert 'name: 笔记助手' not in body
    assert '# 笔记助手' in body
    assert '正文段落。' in body

    # 文件清单含 SKILL.md 与资源文件，按路径排序，带大小。
    import json

    files = json.loads(files_json)
    paths = [f['path'] for f in files]
    assert 'SKILL.md' in paths
    assert 'references/notes.md' in paths
    assert paths == sorted(paths)
    assert all(isinstance(f['size'], int) for f in files)


# --------------------------------------------------------------------------- 2. 真实联调（PG + S3）


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _require_storage(session) -> None:
    """探测 S3 私有桶可写可读，不可达则 skip（区分"存储不可用"与"逻辑 bug"）。"""
    from backend.app.marketplace.storage.s3_storage import marketplace_storage_service

    probe = f'__c2_probe__/{uuid.uuid4().hex}.txt'
    try:
        op, _ = await marketplace_storage_service._get_operator(session, None)
        await op.write(probe, b'1')
        assert await op.read(probe) == b'1'
        await op.delete(probe)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f'S3 存储不可达，跳过真实上传往返: {exc!r}')


async def test_upsert_roundtrip_dedup_and_bump(session) -> None:
    await _require_storage(session)

    hasn_id = f'h_c2_{uuid.uuid4().hex[:8]}'
    user_id = 990000 + (uuid.uuid4().int % 9000)
    slug = f'c2-skill-{uuid.uuid4().hex[:8]}'
    content_v1 = _make_skill_zip(name='C2 技能', description='个人技能库往返测试', slug=slug)

    # upsert：落表 + 传私有桶。
    skill = await personal_skill_service.upsert_from_package(
        session,
        user_id=user_id,
        hasn_id=hasn_id,
        content=content_v1,
        origin='user-upload',
        content_hash='hash-v1',
    )
    assert skill.personal_skill_id == f'user/{hasn_id}/{slug}'
    assert skill.name == 'C2 技能'
    assert skill.description == '个人技能库往返测试'
    assert skill.origin == 'user-upload'
    assert skill.content_hash == 'hash-v1'
    assert skill.version == 1
    assert skill.package_url
    assert skill.body is not None and '# C2 技能' in skill.body
    assert 'references/notes.md' in (skill.files or '')

    # list_mine / get_mine 可见。
    listed = await personal_skill_service.list_mine(session, user_id=user_id)
    assert any(item['personal_skill_id'] == skill.personal_skill_id for item in listed)
    fetched = await personal_skill_service.get_mine(
        session, user_id=user_id, personal_skill_id=skill.personal_skill_id
    )
    assert fetched.personal_skill_id == skill.personal_skill_id

    # 幂等去重：content_hash 未变且已落包 → 版本不 bump。
    again = await personal_skill_service.upsert_from_package(
        session,
        user_id=user_id,
        hasn_id=hasn_id,
        content=content_v1,
        origin='user-upload',
        content_hash='hash-v1',
    )
    assert again.version == 1
    assert again.personal_skill_id == skill.personal_skill_id

    # 内容变更（新 hash）→ 版本 bump 到 2。
    content_v2 = _make_skill_zip(
        name='C2 技能', description='个人技能库往返测试', body_note='第二版正文。', slug=slug
    )
    bumped = await personal_skill_service.upsert_from_package(
        session,
        user_id=user_id,
        hasn_id=hasn_id,
        content=content_v2,
        origin='runtime-auto',
        source_agent_hasn_id='a_self_learn',
        content_hash='hash-v2',
    )
    assert bumped.version == 2
    assert bumped.origin == 'runtime-auto'
    assert bumped.source_agent_hasn_id == 'a_self_learn'
    assert bumped.content_hash == 'hash-v2'

    # get_package_for_owner：从私有桶下回字节，往返为合法 zip 且含最新内容。
    got_skill, got_bytes = await personal_skill_service.get_package_for_owner(
        session, owner_user_id=user_id, personal_skill_id=skill.personal_skill_id
    )
    assert got_skill.personal_skill_id == skill.personal_skill_id
    with zipfile.ZipFile(BytesIO(got_bytes), 'r') as zf:
        names = zf.namelist()
        assert 'SKILL.md' in names
        assert '第二版正文。' in zf.read('SKILL.md').decode('utf-8')

    # 另一 owner 取不到（owner 归属硬隔离）→ 404。
    with pytest.raises(errors.NotFoundError):
        await personal_skill_service.get_package_for_owner(
            session, owner_user_id=user_id + 1, personal_skill_id=skill.personal_skill_id
        )

    # 删除（flush，不 commit；fixture 末尾整体回滚）。
    await personal_skill_service.delete_mine(
        session, user_id=user_id, personal_skill_id=skill.personal_skill_id
    )
    remaining = await session.execute(
        text('SELECT 1 FROM hasn_marketplace.marketplace_personal_skill WHERE personal_skill_id = :pid'),
        {'pid': skill.personal_skill_id},
    )
    assert remaining.scalar_one_or_none() is None


async def test_upsert_rejects_invalid_origin(session) -> None:
    content = _make_skill_zip(name='x', description='y', slug=f'c2-bad-{uuid.uuid4().hex[:8]}')
    with pytest.raises(errors.RequestError):
        await personal_skill_service.upsert_from_package(
            session,
            user_id=991234,
            hasn_id='h_c2_bad',
            content=content,
            origin='not-a-real-origin',
        )


# --------------------------------------------------------------------------- 3. attach 业务语义（fake session）


@dataclass
class _Agent:
    hasn_id: str = 'a_created'
    owner_id: str = 'h_owner'
    agent_name: str = 'agent'
    display_name: str = '云端 Agent'
    skills: Any = field(default_factory=list)
    profile_revision: int = 1
    status: str = 'active'


@dataclass
class _PSkill:
    personal_skill_id: str = 'user/h_owner/c2-skill'


class _Gateway:
    def __init__(self) -> None:
        self.sync_events: list[dict[str, Any]] = []

    async def owns_owner(self, _db: Any, *, owner_id: str, user_id: int) -> bool:
        return owner_id == 'h_owner' and user_id == 100

    async def append_agent_sync_event(self, _db: Any, *, owner_id: str, agent: _Agent, event_type: str) -> None:
        self.sync_events.append({'owner_id': owner_id, 'agent_id': agent.hasn_id, 'event_type': event_type})


class _SeqResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _SeqSession:
    """按序返回预置 execute 结果（_get_owned_agent → 个人技能 select）；flush 计数。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.flush_count = 0

    async def execute(self, _stmt: Any) -> _SeqResult:
        return _SeqResult(self._results.pop(0))

    async def flush(self) -> None:
        self.flush_count += 1


def _service(gateway: _Gateway) -> Any:
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    return HasnAgentProfileService(gateway=gateway)


async def test_attach_personal_skill_appends_and_bumps() -> None:
    gateway = _Gateway()
    agent = _Agent(skills=['old/skill'], profile_revision=4)
    pskill = _PSkill(personal_skill_id='user/h_owner/c2-skill')
    service = _service(gateway)

    response = await service.attach_personal_skill_cloud_first(
        _SeqSession([agent, pskill]),
        owner_id='h_owner',
        hasn_id='a_created',
        personal_skill_id='user/h_owner/c2-skill',
        user_id=100,
    )

    assert agent.skills == ['old/skill', 'user/h_owner/c2-skill']
    assert agent.profile_revision == 5
    assert response.agent.skills == ['old/skill', 'user/h_owner/c2-skill']
    assert gateway.sync_events == [
        {'owner_id': 'h_owner', 'agent_id': 'a_created', 'event_type': 'agent.updated'}
    ]


async def test_attach_personal_skill_is_idempotent() -> None:
    gateway = _Gateway()
    agent = _Agent(skills=['user/h_owner/c2-skill'], profile_revision=7)
    pskill = _PSkill(personal_skill_id='user/h_owner/c2-skill')
    session = _SeqSession([agent, pskill])
    service = _service(gateway)

    await service.attach_personal_skill_cloud_first(
        session,
        owner_id='h_owner',
        hasn_id='a_created',
        personal_skill_id='user/h_owner/c2-skill',
        user_id=100,
    )

    assert agent.skills == ['user/h_owner/c2-skill']
    assert agent.profile_revision == 7  # 不 bump
    assert session.flush_count == 0  # 不落库
    assert gateway.sync_events == []  # 不发事件


async def test_attach_personal_skill_denies_non_owner() -> None:
    gateway = _Gateway()
    service = _service(gateway)

    with pytest.raises(errors.AuthorizationError):
        await service.attach_personal_skill_cloud_first(
            _SeqSession([]),  # owner 校验先于任何 execute 抛错
            owner_id='h_owner',
            hasn_id='a_created',
            personal_skill_id='user/h_owner/c2-skill',
            user_id=999,
        )


async def test_attach_personal_skill_404_when_agent_missing() -> None:
    gateway = _Gateway()
    service = _service(gateway)

    with pytest.raises(errors.NotFoundError):
        await service.attach_personal_skill_cloud_first(
            _SeqSession([None]),  # _get_owned_agent → None
            owner_id='h_owner',
            hasn_id='a_missing',
            personal_skill_id='user/h_owner/c2-skill',
            user_id=100,
        )


async def test_attach_personal_skill_404_when_skill_missing() -> None:
    gateway = _Gateway()
    agent = _Agent(skills=[], profile_revision=1)
    service = _service(gateway)

    with pytest.raises(errors.NotFoundError):
        await service.attach_personal_skill_cloud_first(
            _SeqSession([agent, None]),  # 个人技能查不到
            owner_id='h_owner',
            hasn_id='a_created',
            personal_skill_id='user/h_owner/missing',
            user_id=100,
        )

    assert agent.skills == []  # 未改动
