"""默认 Agent 采用 hub `assistant` 模板：onboarding 创建时把 SOUL/AGENTS/USER + 技能 +
专家头衔(profession) + 头像 物化进 hasn_agents（与 WebUI 手动建 assistant 等价）。

2026-06-15 修正：原先把 `tpl.name`(全能助理) 错当 display_name、漏了 profession/avatar、
skills 写成 `{'enabled': [...]}` 会被 profile 下发端点误读丢弃。
2026-07-12 命名重构（issue②③）：display_name 改为 `{主人昵称}的{专家名称}`（如「福仔的全能助理」），
主人未设昵称（手机号掩码/空）时先以纯专家名占位（全能助理），设昵称后自愈刷新；name_pool 不再参与命名。
正确映射：
  - display_name ← `{主人昵称}的{专家名称}`，全局唯一化（撞名顺延数字后缀）
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


def _patch_avatar(monkeypatch, url: str | None) -> None:
    """monkeypatch 头像生成落桶——返回 url（生成成功）或 None（无 S3/失败→回退模板 icon）。

    本地测试无 S3；直接替换 resolve_generated_avatar_url，使头像断言 hermetic、不误碰 _FakeDB。
    """

    async def _fake_avatar(_db: Any, _seed: str) -> str | None:
        return url

    monkeypatch.setattr(svc.agent_avatar_service, 'resolve_generated_avatar_url', _fake_avatar, raising=True)


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
    """模板存在 + 新建 → display_name=`{昵称}的{专家名}`、tpl.name 作 profession、确定性生成头像、skills 列表。"""
    tpl = _template_stub()

    async def _get_by_id(_db: Any, template_id: str) -> Any:
        assert template_id == DEFAULT_AGENT_TEMPLATE_ID
        return tpl

    async def _get_latest(_db: Any, template_id: str) -> Any:
        return SimpleNamespace(version='2.0.1')

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    monkeypatch.setattr(svc.marketplace_template_version_dao, 'get_latest_by_template', _get_latest, raising=True)
    _patch_uniqueness(monkeypatch, taken=set())  # 星诺 全局未占用
    # 头像生成落桶成功 → avatar 用生成的桶 URL（每人不同、无限不重复），不再取 tpl.icon_url。
    _patch_avatar(monkeypatch, url='https://cdn.example.com/avatars/generated/agent-abc.svg')
    captured = _capture_register(monkeypatch)

    # 新建分支：存在性查询→None，昵称查询→'福仔'
    db: Any = _FakeDB([_Result(None), _Result('福仔')])
    gateway = SqlAlchemyOnboardingGateway()
    agent, created = await gateway.ensure_default_agent(db=db, owner_id='h_owner_1', node_id='n_1')

    assert created is True
    assert agent.hasn_id == 'a_default_test'
    assert captured['agent_name'] == DEFAULT_AGENT_NAME == 'assistant'
    # display_name = `{主人昵称}的{专家名}` = 福仔的全能助理（昵称查询返回「福仔」+ profession 全能助理）。
    assert captured['display_name'] == '福仔的全能助理'
    # 专家头衔来自 tpl.name。
    assert captured['profession'] == '全能助理'
    # 头像 ← 确定性生成头像（落桶），生成失败才回退 tpl.icon_url（见另一 fallback 测试）。
    assert captured['avatar'] == 'https://cdn.example.com/avatars/generated/agent-abc.svg'
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
async def test_ensure_default_agent_avatar_falls_back_to_template_icon(monkeypatch) -> None:
    """头像生成/落桶失败（无 S3 等）→ best-effort 回退 tpl.icon_url，绝不阻断注册。"""
    tpl = _template_stub()

    async def _get_by_id(_db: Any, _tid: str) -> Any:
        return tpl

    async def _get_latest(_db: Any, _tid: str) -> Any:
        return SimpleNamespace(version='2.0.1')

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    monkeypatch.setattr(svc.marketplace_template_version_dao, 'get_latest_by_template', _get_latest, raising=True)
    _patch_uniqueness(monkeypatch, taken=set())
    _patch_avatar(monkeypatch, url=None)  # 生成失败
    captured = _capture_register(monkeypatch)

    db: Any = _FakeDB([_Result(None), _Result('福仔')])
    gateway = SqlAlchemyOnboardingGateway()
    await gateway.ensure_default_agent(db=db, owner_id='h_owner_5', node_id='n_5')

    # 生成失败 → 回退模板 icon（模板存在时不至于让默认分身没头像）。
    assert captured['avatar'] == 'https://cdn.example.com/assistant.png'


@pytest.mark.asyncio
async def test_ensure_default_agent_derives_name_on_global_collision(monkeypatch) -> None:
    """`{昵称}的{专家名}`（福仔的全能助理）已被全局占用（同名两用户）→ 顺延数字后缀，仍每人唯一。"""
    tpl = _template_stub()

    async def _get_by_id(_db: Any, _tid: str) -> Any:
        return tpl

    async def _get_latest(_db: Any, _tid: str) -> Any:
        return SimpleNamespace(version='2.0.1')

    monkeypatch.setattr(svc.marketplace_template_dao, 'get_by_id', _get_by_id, raising=True)
    monkeypatch.setattr(svc.marketplace_template_version_dao, 'get_latest_by_template', _get_latest, raising=True)
    _patch_uniqueness(monkeypatch, taken={'福仔的全能助理'})  # 已被别的同名 owner 占用
    _patch_avatar(monkeypatch, url=None)  # 头像与本用例无关，仅防误碰 _FakeDB
    captured = _capture_register(monkeypatch)

    db: Any = _FakeDB([_Result(None), _Result('福仔')])
    gateway = SqlAlchemyOnboardingGateway()
    await gateway.ensure_default_agent(db=db, owner_id='h_owner_2', node_id=None)

    assert captured['display_name'] == '福仔的全能助理2'
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
    _patch_avatar(monkeypatch, url='https://cdn.example.com/avatars/generated/agent-xyz.svg')
    captured = _capture_register(monkeypatch)

    # 已存在分身：自定义昵称、profession 已有值、avatar 为空、template_id 已有。
    existing = SimpleNamespace(
        display_name='我的小助手',
        profession='私人管家',
        avatar=None,
        template_id=DEFAULT_AGENT_TEMPLATE_ID,
    )
    db: Any = _FakeDB([_Result(existing)])  # 只有存在性查询；昵称查询不会发生
    gateway = SqlAlchemyOnboardingGateway()
    await gateway.ensure_default_agent(db=db, owner_id='h_owner_3', node_id='n_3')

    # 不改名：沿用已存在的 display_name。
    assert captured['display_name'] == '我的小助手'
    # profession 已有值 → 不覆盖（传 None，register 跳过）。
    assert captured['profession'] is None
    # avatar 为空 → 回填确定性生成头像（资产只填空缺不替换）。
    assert captured['avatar'] == 'https://cdn.example.com/avatars/generated/agent-xyz.svg'
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
    _patch_avatar(monkeypatch, url=None)  # 无 S3/生成失败 → 无模板 icon 兜底 → avatar 仍 None
    captured = _capture_register(monkeypatch)

    db: Any = _FakeDB([_Result(None), _Result(None)])  # 存在性→None，昵称→None
    gateway = SqlAlchemyOnboardingGateway()
    agent, created = await gateway.ensure_default_agent(db=db, owner_id='h_owner_4', node_id=None)

    assert created is True
    assert agent.hasn_id == 'a_default_test'
    assert captured['agent_name'] == DEFAULT_AGENT_NAME
    # 模板缺失 → profession=None、昵称查询=None → compute 退化为兜底「AI 分身」占位（仍走唯一化），
    # 不带 persona / profession / avatar；主人设昵称后由自愈刷新（此时无 profession，走 legacy 兜底）。
    assert captured['display_name'] == 'AI 分身'
    assert captured['profession'] is None
    assert captured['avatar'] is None
    assert captured['template_id'] is None
    assert captured['template_version'] is None
    assert captured['soul_md'] is None
    assert captured['skills'] is None
