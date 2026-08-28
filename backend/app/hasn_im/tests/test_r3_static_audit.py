"""R3 静态审计器契约测试。"""

from __future__ import annotations

from pathlib import Path

from backend.scripts.im_r3_static_audit import (
    audit_application,
    find_legacy_event_producers,
    find_legacy_public_moved_table_sql,
    find_legacy_write_routes,
    find_moved_table_writes,
    find_unqualified_moved_table_sql,
)

# 与文件尾 §4b 守卫同一层级口径：parents[3] == backend/，移动本文件必须同改这两处。
_APP_ROOT = Path(__file__).resolve().parents[3] / 'app'


def _write_source(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')


def test_unqualified_moved_table_sql_is_detected(tmp_path: Path) -> None:
    """裸 moved table SQL 必须被发现，显式 schema 名称不得误报。"""
    _write_source(
        tmp_path,
        'backend/app/probe.py',
        """
from sqlalchemy import text

BAD = text("SELECT * FROM hasn_messages")
GOOD_IM = text("SELECT * FROM hasn_im.hasn_messages")
GOOD_OLD = text("SELECT * FROM public.hasn_messages")
""",
    )

    findings = find_unqualified_moved_table_sql(tmp_path / 'backend/app')

    assert [(item.path.name, item.value) for item in findings] == [
        ('probe.py', 'hasn_messages'),
    ]


def test_unqualified_sql_explained_only_in_docstring_is_ignored(
    tmp_path: Path,
) -> None:
    """审计器不得把迁移说明 docstring 误判为运行时 SQL。"""
    _write_source(
        tmp_path,
        'backend/app/probe.py',
        '''
def allocate() -> None:
    """使用 UPDATE hasn_conversations SET current_seq = current_seq + 1 原子取号。"""
''',
    )

    assert find_unqualified_moved_table_sql(tmp_path / 'backend/app') == []


def test_qualified_moved_table_writes_are_detected(tmp_path: Path) -> None:
    """显式 schema 不能成为旧业务代码直写 moved table 的逃逸方式。"""
    _write_source(
        tmp_path,
        'backend/app/legacy_writer.py',
        """
INSERT_MESSAGE = "INSERT INTO public.hasn_messages (id) VALUES (1)"
INSERT_SUPPRESSED = "INSERT INTO public.hasn_suppressed_messages (id) VALUES (1)"
UPDATE_MESSAGE = "UPDATE public.hasn_messages SET content = '{}' WHERE id = 1"
""",
    )

    findings = find_moved_table_writes(tmp_path / 'backend/app')

    assert [(item.path.name, item.value) for item in findings] == [
        ('legacy_writer.py', 'INSERT public.hasn_messages'),
        ('legacy_writer.py', 'INSERT public.hasn_suppressed_messages'),
        ('legacy_writer.py', 'UPDATE public.hasn_messages'),
    ]


def test_im_application_is_the_only_authorized_message_writer(
    tmp_path: Path,
) -> None:
    """汇总审计允许 IM application 写权威表，但拒绝普通业务 service 同样的 SQL。"""
    _write_source(
        tmp_path,
        'backend/app/hasn_im/application/writer.py',
        'SQL = "INSERT INTO hasn_im.hasn_messages (id) VALUES (1)"\n',
    )
    _write_source(
        tmp_path,
        'backend/app/hasn/service/writer.py',
        'SQL = "INSERT INTO public.hasn_messages (id) VALUES (1)"\n',
    )

    findings = audit_application(tmp_path / 'backend/app')

    # 同一条越权写同时命中两类判据：写权限（谁能写）与 legacy public 前缀（切换后表还在不在）。
    assert [(item.path.name, item.kind, item.value) for item in findings] == [
        (
            'writer.py',
            'legacy_public_moved_table_sql',
            'public.hasn_messages',
        ),
        (
            'writer.py',
            'moved_table_write',
            'INSERT public.hasn_messages',
        ),
    ]


def test_legacy_event_producer_is_detected_without_comment_false_positive(
    tmp_path: Path,
) -> None:
    """旧事件字面量生产点必须被发现，普通说明文字不得误报。"""
    _write_source(
        tmp_path,
        'backend/app/producer.py',
        """
OLD_EVENT = "message.received"
NOTE = "message.received 已退役"
NEW_EVENT = "message.new"
""",
    )

    findings = find_legacy_event_producers(tmp_path / 'backend/app')

    assert [(item.path.name, item.value) for item in findings] == [
        ('producer.py', 'message.received'),
    ]


def test_legacy_group_and_unread_write_routes_are_detected(tmp_path: Path) -> None:
    """旧 group/unread 通用写路由必须被发现，只读路由继续允许。"""
    _write_source(
        tmp_path,
        'backend/app/legacy_routes.py',
        """
from fastapi import APIRouter

router = APIRouter()

@router.get("/group/members")
async def list_members():
    return []

@router.post("/group/members")
async def create_member():
    return None

@router.delete("/unread/counts/{item_id}")
async def delete_unread(item_id: int):
    return item_id
""",
    )

    findings = find_legacy_write_routes(tmp_path / 'backend/app')

    assert [(item.path.name, item.value) for item in findings] == [
        ('legacy_routes.py', 'POST /group/members'),
        ('legacy_routes.py', 'DELETE /unread/counts/{item_id}'),
    ]


def test_application_audit_ignores_tests_migrations_and_other_domain_contacts(
    tmp_path: Path,
) -> None:
    """汇总审计只报告运行时 HASN IM 边界，不把测试/迁移/其他领域同名资源算进来。"""
    _write_source(
        tmp_path,
        'backend/app/hasn/tests/test_probe.py',
        'OLD_EVENT = "message.sent"\n',
    )
    _write_source(
        tmp_path,
        'backend/app/hasn/migration/legacy.py',
        'SQL = "SELECT * FROM hasn_contacts"\n',
    )
    _write_source(
        tmp_path,
        'backend/app/hasn_growth/api/business.py',
        """
from fastapi import APIRouter
router = APIRouter()

@router.delete("/admin/contacts/by-email")
async def delete_growth_contact():
    return None
""",
    )

    assert audit_application(tmp_path / 'backend/app') == []


def test_every_im_schema_table_is_in_the_r3_cutover_move_list() -> None:
    """按 IM_SCHEMA 解析的表必须全部进 R3 正反向搬迁清单。

    漏一张就是「切换后代码找 hasn_im.X、库里还在 public.X」→ 端点 500。
    2026-07-31 实测已发生一次：doc03 于 07-29 新增的三张历史快照表没进清单，
    R3 切换后 `/sync/im/bootstrap/start` 全部 500，daemon 换设备/离线后的
    消息历史补拉整条链路失效（真机双设备 E2E 场景 5 因此挂掉）。
    """
    backend_root = Path(__file__).resolve().parents[3]
    model_dir = backend_root / 'app/hasn/model'
    migrations = backend_root / 'sql/hasn/migrations'
    forward = (migrations / '2026-07-16-r2-11-schema-cutover.sql').read_text(encoding='utf-8')
    reverse = (migrations / '2026-07-16-r2-11-schema-cutover.reverse.sql').read_text(encoding='utf-8')

    im_tables: set[str] = set()
    for path in model_dir.glob('*.py'):
        source = path.read_text(encoding='utf-8')
        if 'IM_SCHEMA' not in source:
            continue
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith('__tablename__'):
                im_tables.add(stripped.split("'")[1])
                break

    assert im_tables, '未能从模型中解析出任何 IM_SCHEMA 表，守卫失效'

    # 集成事件/消费者三表在迁移中改名去前缀，由 §4b 单独处理，不走数组清单。
    renamed = {
        'hasn_im_integration_events',
        'hasn_im_event_consumer_offsets',
        'hasn_im_event_consumer_failures',
    }
    for table in sorted(im_tables - renamed):
        assert f"'{table}'" in forward, f'{table} 未进 R3 正向搬迁清单，切换后代码会找不到它'
        assert f"'{table}'" in reverse, f'{table} 未进 R3 反向搬迁清单，回滚后会留在 hasn_im'


def test_legacy_public_prefix_on_moved_table_is_detected(tmp_path: Path) -> None:
    """写死 ``public.`` 的 moved table SQL 必须被发现——读写都算，非 moved 表不得误报。

    ``find_unqualified_moved_table_sql`` 只判「有没有写 schema」，它的自测把
    ``public.hasn_messages`` 明确列为 GOOD；判不出「写的这个 schema 切换后还在不在」的正是本函数。
    """
    _write_source(
        tmp_path,
        'backend/app/probe.py',
        """
from sqlalchemy import text

BAD_READ = text("SELECT * FROM public.hasn_messages WHERE id = :id")
BAD_JOIN = text("SELECT 1 FROM t JOIN public.hasn_conversations c ON c.id = t.cid")
BAD_WRITE = text("DELETE FROM public.hasn_unread_projection WHERE id = :id")
GOOD_IM = text("SELECT * FROM hasn_im.hasn_messages")
GOOD_OTHER_TABLE = text("SELECT * FROM public.hasn_agents")
""",
    )

    findings = find_legacy_public_moved_table_sql(tmp_path / 'backend/app')

    assert [(item.path.name, item.value) for item in findings] == [
        ('probe.py', 'public.hasn_messages'),
        ('probe.py', 'public.hasn_conversations'),
        ('probe.py', 'public.hasn_unread_projection'),
    ]


def test_runtime_moved_table_sql_has_no_legacy_public_prefix() -> None:
    """运行时 moved table 上的 legacy ``public.`` 前缀必须清零。

    生产 ``HASN_IM_SCHEMA_CUTOVER=true``、本机默认 false，写死 ``public.`` 只在生产
    ``UndefinedTableError``——本机永远绿。这条断言是唯一会在合入前红出来的地方。
    """
    findings = [
        item
        for item in find_legacy_public_moved_table_sql(_APP_ROOT)
        if 'tests' not in item.path.parts and 'migration' not in item.path.parts
    ]

    detail = [(str(item.path), item.line, item.value) for item in findings]
    assert findings == [], f'moved table 上仍有写死的 public. 前缀：{detail}'
