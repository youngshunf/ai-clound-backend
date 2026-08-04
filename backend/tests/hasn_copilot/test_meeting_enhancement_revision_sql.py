"""会议增强候选 revision 的 PostgreSQL 契约测试。"""

from pathlib import Path

SQL_PATH = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_copilot' / 'meeting_enhancement_revisions.sql'


def test_revision_sql_declares_authority_and_concurrency_constraints() -> None:
    assert SQL_PATH.exists(), '必须先提供会议增强候选 revision 的 PostgreSQL SQL'
    sql = SQL_PATH.read_text(encoding='utf-8')
    assert '"realtime_revision_id"' in sql
    assert '"preferred_enhancement_revision_id"' in sql
    assert '"meeting_enhancement_revisions"' in sql
    assert '"supersedes"' in sql
    assert 'WHERE "status" = \'pending_confirmation\'' in sql
    assert '"owner_hasn_id"' in sql
