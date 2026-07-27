"""R3 生产硬切换就绪守卫。

这些测试只校验切换后必须成立的稳定接缝，不连接生产环境：

- 目标 schema/table 名称由同一开关显式选择，禁止依赖 ``search_path``；
- IM 网关固定使用受限 IM role 的 session maker；
- 三个事件消费者有独立 worker 装配，不能只存在未运行的类；
- 生产切换配置缺失角色 DSN 或最低 daemon 版本时必须拒绝启动。
"""

from __future__ import annotations

import os
import subprocess
import sys

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnMessages
from backend.app.hasn_im.adapters.sqlalchemy_relation_gateway import (
    SqlAlchemyRelationGateway,
)
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.application.provider import get_im_gateway, get_relation_gateway
from backend.core.conf import Settings
from backend.database.db import im_service_db_session

_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_cutover_table_names_are_explicitly_schema_qualified() -> None:
    """切换开关打开后必须生成目标 schema 名称，不能靠 search_path 猜表。"""
    from backend.database.schema_names import SchemaNames

    names = SchemaNames(cutover=True)
    assert names.im_table('hasn_messages') == 'hasn_im.hasn_messages'
    assert names.im_event_table('integration_events') == 'hasn_im.integration_events'
    assert names.sync_table('hasn_sync_events') == 'hasn_sync.hasn_sync_events'


def test_im_gateway_uses_im_service_sessionmaker() -> None:
    """IM 写入口必须使用 IM role 的独立连接池。"""
    gateway = get_im_gateway()
    assert isinstance(gateway, PythonLocalImGateway)
    assert gateway.session_factory is im_service_db_session


def test_relation_gateway_uses_im_service_sessionmaker() -> None:
    """关系写入口同样只能使用 IM role 的独立连接池。"""
    gateway = get_relation_gateway()
    assert isinstance(gateway, SqlAlchemyRelationGateway)
    assert gateway.session_factory is im_service_db_session


def test_consumer_worker_builds_all_three_runners() -> None:
    """R3 首批三个消费者必须真正装配进独立 worker。"""
    from backend.app.hasn_im.consumer_worker import build_runners

    runners = build_runners(instance_id='test-worker')
    assert [runner.name for runner in runners] == [
        'sync_projector',
        'realtime_notifier',
        'push_notifier',
    ]


@pytest.mark.asyncio
async def test_message_commit_appends_one_integration_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发送事务只追加一条集成事件，扇出由消费者完成。"""
    from backend.app.hasn_im.application import message_service
    from backend.app.hasn_im.consumers.facts import IM_MESSAGE_COMMITTED

    captured: dict = {}

    async def capture(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(message_service, 'append_event', capture)
    msg = HasnMessages(
        conversation_id='conv-r3',
        from_id='h_sender',
        to_id='h_recipient',
        conversation_seq=7,
        content={'text': 'R3'},
        content_type=1,
        local_id='local-r3',
    )
    msg.id = 123
    msg.created_time = datetime(2026, 7, 27, tzinfo=UTC)
    await message_service._append_message_committed_event(
        cast(AsyncSession, object()),
        conversation_id='conv-r3',
        sender_hasn_id='h_sender',
        msg=msg,
        origin_node_id='node-r3',
        origin_session_id='session-r3',
    )

    assert captured['event_type'] == IM_MESSAGE_COMMITTED
    assert captured['aggregate_id'] == 'conv-r3'
    assert captured['aggregate_seq'] == 7
    assert captured['payload']['message_id'] == '123'
    assert captured['payload']['content_body'] == {'text': 'R3'}


def test_prod_cutover_rejects_missing_role_dsns_and_daemon_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生产硬切换不可带空 DSN 或关闭旧 daemon 版本闸启动。"""
    from backend.core.conf import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv('ENVIRONMENT', 'prod')
    monkeypatch.setenv('HASN_IM_SCHEMA_CUTOVER', 'true')
    monkeypatch.setenv('IM_SERVICE_DATABASE_URL', '')
    monkeypatch.setenv('SYNC_SERVICE_DATABASE_URL', '')
    monkeypatch.setenv('PYTHON_BACKEND_DATABASE_URL', '')
    monkeypatch.setenv('HASN_WS_MIN_CLIENT_VERSION', '')

    with pytest.raises(ValidationError, match='R3 生产硬切换配置不完整'):
        Settings()


def test_cutover_route_enumeration_has_no_generic_im_write_bypass() -> None:
    """在真实切换配置下枚举四个 scope，旧通用 IM 写路由必须物理不可达。"""
    code = """
from backend.app.hasn.api.router import agent, app, open_api, v1

mutating = {"POST", "PUT", "PATCH", "DELETE"}
routers = (v1, app, agent, open_api)
offenders = []
business_contact_module = "backend.app.hasn.api.v1.app.contacts"
business_group_module = "backend.app.hasn.api.v1.app.hasn_groups"

for router in routers:
    for route in router.routes:
        methods = set(getattr(route, "methods", set())) & mutating
        if not methods:
            continue
        path = route.path
        endpoint_module = getattr(getattr(route, "endpoint", None), "__module__", "")
        generic_path = (
            "/unread/counts" in path
            or "/group/members" in path
            or path.endswith("/messages/send")
            or path.endswith("/inbox/pull")
            or (
                ("/hasn/contacts" in path or "/hasn/contact-requests" in path)
                and "/hasn/app/contacts" not in path
            )
        )
        wrong_app_contact = (
            "/hasn/app/contacts" in path
            and endpoint_module != business_contact_module
        )
        wrong_app_group = (
            "/hasn/app/groups" in path
            and endpoint_module != business_group_module
        )
        open_im_write = (
            "/hasn/open/" in path
            and any(token in path for token in ("contact", "group", "unread"))
        )
        if generic_path or wrong_app_contact or wrong_app_group or open_im_write:
            offenders.append(
                {
                    "methods": sorted(methods),
                    "path": path,
                    "module": endpoint_module,
                }
            )

if offenders:
    raise SystemExit(f"发现旧通用 IM 写路由：{offenders!r}")
"""
    env = os.environ.copy()
    env['HASN_IM_SCHEMA_CUTOVER'] = 'true'
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_cutover_keeps_daemon_conversation_ensure_contract() -> None:
    """R3 摘除旧会话 CRUD 后仍须保留 daemon 的稳定 ensure 契约。"""
    code = """
from backend.app.hasn.api.router import app

matches = [
    route
    for route in app.routes
    if route.path == "/api/v1/hasn/app/conversations/ensure"
    and "POST" in set(getattr(route, "methods", set()))
]
if len(matches) != 1:
    raise SystemExit(f"ensure 路由数量错误：{len(matches)}")
module = getattr(matches[0].endpoint, "__module__", "")
if module != "backend.app.hasn.api.v1.app.hasn_im":
    raise SystemExit(f"ensure 未经 R3 ImGateway 端点承载：{module}")
"""
    env = os.environ.copy()
    env['HASN_IM_SCHEMA_CUTOVER'] = 'true'
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
