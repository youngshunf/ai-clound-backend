"""HASN 信任等级升级迁移脚本 (阶段二)

从旧的 0-4 体系迁移至新的 0-5 体系：
  - 旧 trust_level=3 (trusted) → 新 trust_level=4 (密友)
  - 旧 trust_level=4 (owner)   → 新 trust_level=5 (所有者)

同时更新 isn_contacts.trust_level 注释, hasn_schema 等元数据

用法:
  cd huanxing-cloud-backend
  python -m backend.app.hasn.migration.v2_trust_level_upgrade
"""
import asyncio
import logging

from sqlalchemy import text

from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

UPGRADE_SQL = """
-- 阶段二: 信任等级升位 (旧 0-4 → 新 0-5)
-- 注意: 必须先迁移 4→5，再迁移 3→4，避免覆盖

BEGIN;

-- 1. 旧 owner (4) → 新 所有者 (5)
UPDATE hasn_contacts
SET trust_level = 5
WHERE trust_level = 4;

-- 2. 旧 trusted (3) → 新 密友 (4)
UPDATE hasn_contacts
SET trust_level = 4
WHERE trust_level = 3;

-- 3. 更新字段注释
COMMENT ON COLUMN hasn_contacts.trust_level IS
  '信任等级 (0:已拉黑:red/1:陌生人:gray/2:普通联系人:blue/3:朋友:green/4:密友:orange/5:所有者:purple)';

COMMIT;
"""

ROLLBACK_SQL = """
-- 回滚: 新 0-5 → 旧 0-4
BEGIN;

-- 1. 新 所有者 (5) → 旧 owner (4)
UPDATE hasn_contacts
SET trust_level = 4
WHERE trust_level = 5;

-- 2. 新 密友 (4) → 旧 trusted (3)
UPDATE hasn_contacts
SET trust_level = 3
WHERE trust_level = 4;

-- 3. 还原字段注释
COMMENT ON COLUMN hasn_contacts.trust_level IS
  '信任等级 (0:已拉黑:red/1:陌生人:gray/2:普通好友:blue/3:信任好友:green/4:所有者:purple)';

COMMIT;
"""


async def run_upgrade() -> None:
    """执行信任等级升位迁移"""
    async with async_db_session() as db:
        # 迁移前查统计
        result = await db.execute(
            text("SELECT trust_level, COUNT(*) as cnt FROM hasn_contacts GROUP BY trust_level ORDER BY trust_level")
        )
        rows = result.fetchall()
        logger.info("迁移前统计:")
        for row in rows:
            logger.info(f"  trust_level={row[0]}: {row[1]} 条记录")

        # 执行升位
        for stmt in UPGRADE_SQL.strip().split(';'):
            stmt = stmt.strip()
            if stmt and not stmt.startswith('--'):
                await db.execute(text(stmt))

        # 迁移后查统计
        result = await db.execute(
            text("SELECT trust_level, COUNT(*) as cnt FROM hasn_contacts GROUP BY trust_level ORDER BY trust_level")
        )
        rows = result.fetchall()
        logger.info("迁移后统计:")
        for row in rows:
            label_map = {0: 'blocked', 1: 'stranger', 2: 'normal', 3: 'friend', 4: 'trusted', 5: 'owner'}
            logger.info(f"  trust_level={row[0]} ({label_map.get(row[0], '?')}): {row[1]} 条记录")

    logger.info("✅ 信任等级升级完成")


async def run_rollback() -> None:
    """回滚信任等级迁移"""
    async with async_db_session() as db:
        for stmt in ROLLBACK_SQL.strip().split(';'):
            stmt = stmt.strip()
            if stmt and not stmt.startswith('--'):
                await db.execute(text(stmt))
    logger.info("✅ 信任等级回滚完成")


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)

    mode = sys.argv[1] if len(sys.argv) > 1 else 'upgrade'
    if mode == 'rollback':
        asyncio.run(run_rollback())
    else:
        asyncio.run(run_upgrade())
