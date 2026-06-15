"""记忆独立模块与 schema 拆分（ADR-15，记忆实施 95）静态契约测试。

不连库、不起 HTTP——纯导入 + metadata/源码内省，验收 doc 95 §6：
- 7 张记忆表在 metadata 中 schema 全为 `hasn_memory`，无 public 旧表名残留；
- owner_memory 用户端 URL（`/memory`、`/memory/contributions`）不变；
- `app/hasn` 旧位置 re-export shim 兼容（model/service/api/schema）；
- 裸 SQL 已全限定 `hasn_memory.namespace_revision`（无 `public.memory_namespace_revisions`）；
- 迁移 SQL 覆盖 7 张表（SET SCHEMA + 去前缀 RENAME）。
"""

from __future__ import annotations

from pathlib import Path

REPO_BACKEND = Path(__file__).resolve().parents[2]

# hasn_memory schema 内的 7 张表（去前缀）
MEMORY_TABLES = {
    'owner_memory',
    'owner_memory_contribution',
    'namespace_revision',
    'episodic_turn',
    'semantic_fact',
    'memory_event',
    'extraction_job',
}

# 原 public 旧表名——迁移后不应再出现在 metadata
STALE_PUBLIC_NAMES = {
    'hasn_owner_memory',
    'hasn_owner_memory_contribution',
    'memory_namespace_revisions',
    'episodic_turns',
    'semantic_facts',
    'memory_events',
    'memory_extraction_jobs',
}


def _metadata():
    # 导入全量 router 树，触发所有 model 注册进 MappedBase.metadata
    import backend.app.router  # noqa: F401
    from backend.common.model import MappedBase

    return MappedBase.metadata


def test_all_memory_tables_in_hasn_memory_schema():
    md = _metadata()
    by_name = {t.name: t for t in md.sorted_tables}
    for name in MEMORY_TABLES:
        assert name in by_name, f'记忆表 {name} 未注册进 metadata'
        assert by_name[name].schema == 'hasn_memory', (
            f'记忆表 {name} schema={by_name[name].schema!r}，应为 hasn_memory'
        )


def test_no_stale_public_memory_tables_in_metadata():
    md = _metadata()
    names = {t.name for t in md.sorted_tables}
    leaked = names & STALE_PUBLIC_NAMES
    assert not leaked, f'metadata 仍含 public 旧记忆表名（应已去前缀迁入 hasn_memory）：{sorted(leaked)}'


def test_owner_memory_routes_preserved():
    from backend.app.hasn_memory.api.v1.app.owner_memory import router

    paths = {r.path for r in router.routes}
    assert paths == {'/memory', '/memory/contributions'}, f'owner_memory 路由漂移：{sorted(paths)}'


def test_legacy_shims_reexport():
    # model shim
    from backend.app.hasn.model.hasn_owner_memory import HasnOwnerMemory, HasnOwnerMemoryContribution
    from backend.app.hasn_memory.model import HasnOwnerMemory as NewOM
    from backend.app.hasn_memory.model import HasnOwnerMemoryContribution as NewOMC

    assert HasnOwnerMemory is NewOM
    assert HasnOwnerMemoryContribution is NewOMC

    # service shim（单例同一对象）
    from backend.app.hasn.service.owner_memory_service import owner_memory_service as legacy_svc
    from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service as new_svc

    assert legacy_svc is new_svc

    # api shim（router 同一对象）
    from backend.app.hasn.api.v1.app.owner_memory import router as legacy_router
    from backend.app.hasn_memory.api.v1.app.owner_memory import router as new_router

    assert legacy_router is new_router

    # schema shim（DTO 同一类）
    from backend.app.hasn.schema.hasn_agents import OwnerMemoryResponse as LegacyResp
    from backend.app.hasn_memory.schema.owner_memory import OwnerMemoryResponse as NewResp

    assert LegacyResp is NewResp


def test_owner_memory_models_use_hasn_memory_base():
    from backend.app.hasn_memory.model._base import APP_SCHEMA, HasnMemoryBase
    from backend.app.hasn_memory.model import HasnOwnerMemory, HasnOwnerMemoryContribution

    assert APP_SCHEMA == 'hasn_memory'
    assert issubclass(HasnOwnerMemory, HasnMemoryBase)
    assert issubclass(HasnOwnerMemoryContribution, HasnMemoryBase)
    assert HasnOwnerMemory.__table__.schema == 'hasn_memory'
    assert HasnOwnerMemory.__tablename__ == 'owner_memory'
    assert HasnOwnerMemoryContribution.__tablename__ == 'owner_memory_contribution'


def test_sync_service_raw_sql_fully_qualified():
    src = (REPO_BACKEND / 'app' / 'hasn' / 'service' / 'hasn_sync_service.py').read_text(encoding='utf-8')
    assert 'public.memory_namespace_revisions' not in src, '裸 SQL 仍引用 public.memory_namespace_revisions（搬 schema 后失效）'
    assert 'hasn_memory.namespace_revision' in src, '裸 SQL 应全限定 hasn_memory.namespace_revision'


def test_migration_sql_covers_all_seven_tables():
    mig = (
        REPO_BACKEND
        / 'sql'
        / 'hasn_memory'
        / 'migrations'
        / '2026-06-15-move-memory-tables-to-hasn-memory-schema.sql'
    ).read_text(encoding='utf-8')
    assert 'CREATE SCHEMA IF NOT EXISTS hasn_memory' in mig
    for old in STALE_PUBLIC_NAMES:
        assert old in mig, f'迁移 SQL 缺少旧表名 {old}'
    for new in MEMORY_TABLES:
        assert new in mig, f'迁移 SQL 缺少新表名 {new}'
