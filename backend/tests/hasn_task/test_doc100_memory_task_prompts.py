"""doc100：内置任务提示词必须按本地记忆单源完整重写。"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / 'sql' / 'hasn_task' / 'migrations'


def test_memory_review_revision_two_teaches_local_channel() -> None:
    sql = (MIGRATIONS / '2026-08-02-memory-review-local-authority-r2.sql').read_text(encoding='utf-8')
    assert 'revision = 2' in sql
    assert 'hasn.local.tool.call' in sql
    assert '被拒绝是正常结果' in sql
    assert '不要重试' in sql


def test_daily_briefing_r9_is_full_rewrite_and_uses_local_save() -> None:
    sql = (MIGRATIONS / '2026-08-02-daily-briefing-local-memory-r9.sql').read_text(encoding='utf-8')
    assert 'system_prompt = $briefing$' in sql
    assert 'system_prompt ||' not in sql
    assert 'revision = 9' in sql
    assert 'hasn.local.tool.call' in sql
    assert 'hasn.memory.save' in sql
    assert 'subject_kind' in sql and 'owner' in sql
    assert 'owner_memory.version' in sql
    assert 'hasn.owner.memory.contribute' not in sql
    assert 'hasn://artifact/{id}' in sql
