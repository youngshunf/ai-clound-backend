"""R3 静态审计器契约测试。"""

from __future__ import annotations

from pathlib import Path

from backend.scripts.im_r3_static_audit import (
    audit_application,
    find_legacy_event_producers,
    find_legacy_write_routes,
    find_moved_table_writes,
    find_unqualified_moved_table_sql,
)


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

    assert [(item.path.name, item.kind, item.value) for item in findings] == [
        (
            'writer.py',
            'moved_table_write',
            'INSERT public.hasn_messages',
        )
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
