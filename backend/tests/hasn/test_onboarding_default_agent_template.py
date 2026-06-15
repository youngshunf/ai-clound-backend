"""默认 Agent 采用 hub `assistant` 模板：onboarding 创建时把 SOUL/AGENTS/USER + 技能 +
专家头衔(profession) + 头像 物化进 hasn_agents（与 WebUI 手动建 assistant 等价）。

2026-06-15 修正：原先把 `tpl.name`(全能助理) 错当 display_name、漏了 profession/avatar、
skills 写成 `{'enabled': [...]}` 会被 profile 下发端点误读丢弃。正确映射：
  - display_name ← name_pool 首位（星诺），全局唯一化（撞名走「主人昵称」派生）
  - profession   ← tpl.name（全能助理）
  - avatar       ← tpl.icon_url
  - skills       ← list[str]

测试策略：fake db（区分存在性/昵称两次 execute）+ monkeypatch is_display_name_taken /
register_hasn_agent，捕获 gateway 转发给 register 的 kwargs，断言映射正确，并覆盖：
模板缺失回退、已存在分身「只回填空字段、不改名/不 clobber」。register 自身落库/幂等在
hasn_auth 层 + 真实 DB 集成验证覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.hasn.service import hasn_onboarding_service as svc
from backend.app.hasn.service.hasn_agents_service import SqlAlchemyAgentProfileGateway
from backend.app.hasn.service.hasn_onboarding_service import (
    DEFAULT_AGENT_DISPLAY_NAME,
    DEFAULT_AGENT_NAME,
    DEFAULT_AGENT_TEMPLATE_ID,
    SqlAlchemyOnboardingGateway,
)


def _template_stub() -> SimpleNamespace:
    """模拟 marketplace_template 里 huanxing/agent/assistant 行（name=专家头衔、name_pool=昵称池）。"""
    return SimpleNamespace(
        template_id=DEFAULT_AGENT_TEMPLATE_ID,
        name='全能助理',  # 专家头衔 → profession
        name_pool='星诺,知微,随安',  # 昵称池，首位是 display_name 基名
        icon_url='https://cdn.example.com/assistant.png',
        description='您专属的顶级执行管家',
        soul_md='# SOUL.md — 我是星诺 💎',
        agents_md='# AGENTS.md',
        user_md='# USER.md',
        memory_md='# MEMORY.md',
        skill_dependencies='huanxing/utility/weather, huanxing/utility/calculator',
    )


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeDB:
    """按顺序返回预设 execute 结果：新建分支 2 次（存在性→None、昵称），已存在分支 1 次。"""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)

    async def execute(self, *_a: Any, **_k: Any) -> _Result:
        return self._results.pop(0)

    async def flush(self) -> None:
        return None


def _patch_uniqueness(monkeypatch, taken: set[str]) -> None:
    async def _fake_taken(_db: Any, display_name: str) -> bool:
        return display_name in taken

    monkeypatch.setattr(SqlAlchemyAgentProfileGateway, 'is_display_name_taken', staticmethod(_fake_taken))


def _capture_register(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_register(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            'agent': SimpleNamespace(hasn_id='a_default_test'),
            'agent_key': None,
            'already_exists': False,
        }

    monkeypatch.setattr(svc.hasn_auth_service, 'register_hasn_agent', _fake_register, raising=True)
    return captured


@pytest.mark.asyncio
async def test_ensure_default_agent_materializes_assistant_template(monkeypatch) -> None:
    """模板存在 + 新建 → name_pool 首位作昵称、tpl.name 作 profession、icon_url 作头像、skills 列表。"""
    tpl = _template_stub()

    async def _get_by_id(_db: Any, template_id: str) -> Any:
        assert template_id == DEFAULT_AGENT_TEMPLATE_ID
        return tpl

    async def _get_latest(_db: Any, template_id: str) -> Any:
        return SimpleNamespace(version='2.0.1')

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    monkeypatch.setattr(svc.marketplace_template_version_dao, 'get_latest_by_template', _get_latest, raising=True)
    _patch_uniqueness(monkeypatch, taken=set())  # 星诺 全局未占用
    captured = _capture_register(monkeypatch)

    # 新建分支：存在性查询→None，昵称查询→'福仔'
    db = _FakeDB([_Result(None), _Result('福仔')])
    gateway = SqlAlchemyOnboardingGateway()
    agent, created = await gateway.ensure_default_agent(db=db, owner_id='h_owner_1', node_id='n_1')

    assert created is True
    assert agent.hasn_id == 'a_default_test'
    assert captured['agent_name'] == DEFAULT_AGENT_NAME == 'assistant'
    # display_name = name_pool 首位（星诺），而不是 tpl.name（全能助理）。
    assert captured['display_name'] == '星诺'
    # 专家头衔来自 tpl.name。
    assert captured['profession'] == '全能助理'
    # 头像来自 tpl.icon_url。
    assert captured['avatar'] == 'https://cdn.example.com/assistant.png'
    assert captured['template_id'] == DEFAULT_AGENT_TEMPLATE_ID
    assert captured['template_version'] == '2.0.1'
    assert captured['soul_md'] == tpl.soul_md
    assert captured['agents_md'] == tpl.agents_md
    assert captured['user_md'] == tpl.user_md
    assert captured['memory_md'] == tpl.memory_md
    # 技能装配：逗号分隔 → list[str]（不再 {'enabled': [...]}，否则下发端点会误读丢弃）。
    assert captured['skills'] == ['huanxing/utility/weather', 'huanxing/utility/calculator']
    assert captured['capabilities'] == [svc.DEFAULT_AGENT_TEMPLATE]
    assert captured['role'] == 'primary'
    assert captured['created_via'] == 'onboarding'


@pytest.mark.asyncio
async def test_ensure_default_agent_derives_name_on_global_collision(monkeypatch) -> None:
    """基名（星诺）已被全局占用 → 用「主人昵称」派生（星诺·福仔），不再每个用户都同名。"""
    tpl = _template_stub()

    async def _get_by_id(_db: Any, _tid: str) -> Any:
        return tpl

    async def _get_latest(_db: Any, _tid: str) -> Any:
        return SimpleNamespace(version='2.0.1')

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    monkeypatch.setattr(svc.marketplace_template_version_dao, 'get_latest_by_template', _get_latest, raising=True)
    _patch_uniqueness(monkeypatch, taken={'星诺'})  # 星诺 已被别的 owner 占用
    captured = _capture_register(monkeypatch)

    db = _FakeDB([_Result(None), _Result('福仔')])
    gateway = SqlAlchemyOnboardingGateway()
    await gateway.ensure_default_agent(db=db, owner_id='h_owner_2', node_id=None)

    assert captured['display_name'] == '星诺·福仔'
    assert captured['profession'] == '全能助理'


@pytest.mark.asyncio
async def test_ensure_default_agent_existing_only_backfills_empty(monkeypatch) -> None:
    """已存在默认分身 → 不改名、不 clobber skills/persona；只回填当前为空的 profession/avatar。"""
    tpl = _template_stub()

    async def _get_by_id(_db: Any, _tid: str) -> Any:
        return tpl

    async def _get_latest(_db: Any, _tid: str) -> Any:
        return SimpleNamespace(version='2.0.1')

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    monkeypatch.setattr(svc.marketplace_template_version_dao, 'get_latest_by_template', _get_latest, raising=True)
    _patch_uniqueness(monkeypatch, taken=set())
    captured = _capture_register(monkeypatch)

    # 已存在分身：自定义昵称、profession 已有值、avatar 为空、template_id 已有。
    existing = SimpleNamespace(
        display_name='我的小助手',
        profession='私人管家',
        avatar=None,
        template_id=DEFAULT_AGENT_TEMPLATE_ID,
    )
    db = _FakeDB([_Result(existing)])  # 只有存在性查询；昵称查询不会发生
    gateway = SqlAlchemyOnboardingGateway()
    await gateway.ensure_default_agent(db=db, owner_id='h_owner_3', node_id='n_3')

    # 不改名：沿用已存在的 display_name。
    assert captured['display_name'] == '我的小助手'
    # profession 已有值 → 不覆盖（传 None，register 跳过）。
    assert captured['profession'] is None
    # avatar 为空 → 一次性回填模板头像。
    assert captured['avatar'] == 'https://cdn.example.com/assistant.png'
    # skills / persona / description 一律不动（避免 clobber 用户自定义）。
    assert captured['skills'] is None
    assert captured['soul_md'] is None
    assert captured['agents_md'] is None
    assert captured['user_md'] is None
    assert captured['description'] is None
    # template_id 已有 → 不重复回填。
    assert captured['template_id'] is None


@pytest.mark.asyncio
async def test_ensure_default_agent_falls_back_when_template_missing(monkeypatch) -> None:
    """模板缺失（云端尚未 sync）→ 不阻断 onboarding，退回纯身份创建（零 fake）。"""

    async def _get_by_id(_db: Any, _template_id: str) -> Any:
        return None

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    _patch_uniqueness(monkeypatch, taken=set())
    captured = _capture_register(monkeypatch)

    db = _FakeDB([_Result(None), _Result(None)])  # 存在性→None，昵称→None
    gateway = SqlAlchemyOnboardingGateway()
    agent, created = await gateway.ensure_default_agent(db=db, owner_id='h_owner_4', node_id=None)

    assert created is True
    assert agent.hasn_id == 'a_default_test'
    assert captured['agent_name'] == DEFAULT_AGENT_NAME
    # 模板缺失 → 用兜底 display_name（仍走唯一化），且不带 persona / profession / avatar。
    assert captured['display_name'] == DEFAULT_AGENT_DISPLAY_NAME
    assert captured['profession'] is None
    assert captured['avatar'] is None
    assert captured['template_id'] is None
    assert captured['template_version'] is None
    assert captured['soul_md'] is None
    assert captured['skills'] is None
