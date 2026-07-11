"""幂等补齐后端 model 声明的全部 PostgreSQL schema（CREATE SCHEMA IF NOT EXISTS）。

背景：fba 启动 lifespan 用 `MappedBase.metadata.create_all` 建表，但 SQLAlchemy **不会**自动
建 schema。各业务域 model 用 `schema='hasn_xxx'` 把表分域到独立 PostgreSQL schema；新库 /
重装 / `fba init` 未覆盖的新域，若对应 schema 不存在，`create_all` 建表即抛
`asyncpg.exceptions.InvalidSchemaNameError`（"模式 xxx 不存在"）→ ASGI lifespan startup failed
→ worker 崩、granian 退出，后端"起来一瞬又崩"。

本脚本在启动后端前跑一次即可根治：import 全部 model 填充 `MappedBase.metadata` → 收集其中
声明的所有非 public schema → 幂等 `CREATE SCHEMA IF NOT EXISTS`。schema 集合直接取自
create_all 用的同一份 metadata，**永不漂移**（新增业务域自动纳入，无需维护清单）。

连接参数复用 fba 的 `async_engine`（由 `settings` 从 .env 拼好，指向本地/生产 huanxing 库）。

用法：
    uv run python -m backend.scripts.ensure_schemas          # 幂等，可重复执行
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil

from pathlib import Path

import sqlalchemy as sa

from backend.common.model import MappedBase
from backend.database.db import async_engine


def _import_all_models() -> None:
    """遍历 backend/app/*/model 下所有模块并 import，填充 MappedBase.metadata。

    只 import model 层（纯 SQLAlchemy 表定义，无 redis/DB 连接副作用），不启动整个 fba app。
    单个模块 import 失败不致命（打印告警继续），避免个别历史模块拖垮整体。
    """
    app_root = Path(__file__).resolve().parents[1] / 'app'
    for model_dir in sorted(app_root.glob('*/model')):
        base_pkg = f'backend.app.{model_dir.parent.name}.model'
        try:
            pkg = importlib.import_module(base_pkg)
        except Exception as exc:  # noqa: BLE001
            print(f'  [warn] 跳过 {base_pkg}: {exc!r}')
            continue
        # model 常按表拆成多个文件，逐个 import 子模块兜底（__init__ 未必导全）
        for mod in pkgutil.iter_modules(pkg.__path__):
            try:
                importlib.import_module(f'{base_pkg}.{mod.name}')
            except Exception as exc:  # noqa: BLE001
                print(f'  [warn] 跳过 {base_pkg}.{mod.name}: {exc!r}')


async def main() -> int:
    _import_all_models()

    # 取自 create_all 用的同一份 metadata：每张表的 .schema 即需要预建的 schema（public/None 跳过）
    schemas = sorted({t.schema for t in MappedBase.metadata.tables.values() if t.schema})
    if not schemas:
        print('[X] 未从 metadata 收集到任何 schema —— 可能 model 未成功 import，终止（不建 0 个）')
        return 1

    async with async_engine.begin() as conn:
        rows = await conn.execute(sa.text('select schema_name from information_schema.schemata'))
        before = set(rows.scalars().all())
        for s in schemas:
            # schema 名来自 model 常量（非用户输入）；标识符不可参数化，用双引号包裹
            await conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{s}"'))

    created = [s for s in schemas if s not in before]
    print(f'[OK] schema 已就绪，共 {len(schemas)} 个；本次新建 {len(created)} 个: {created or "无"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
