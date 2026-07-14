"""一次性回填：把存量 hasn_nodes 中「ip_address 有值但 ip_location 为空」的行补上归属地。

背景：ip_location 依赖的 GeoLite2 mmdb 从未部署，历史行归属地全 NULL（2026-07-14 福仔发现）；
现已切换 ip2region 离线库（xdb 进仓库 backend/data/，零部署前提），新握手会自动回填，
本脚本只负责收敛存量行。幂等：只挑 ip_location IS NULL 的行；解析不出（私网/库内查不到）
的行如实跳过留空，**绝不伪造城市**。

用法（先本地 PG 15432，生产 huanxing 库同样跑一次）：
    uv run python -m backend.scripts.backfill_node_ip_location --dry-run   # 只看不写
    uv run python -m backend.scripts.backfill_node_ip_location            # 真跑提交
"""

from __future__ import annotations

import argparse
import asyncio

import sqlalchemy as sa

from backend.app.hasn.model.hasn_nodes import HasnNodes
from backend.app.hasn.service import geoip_service
from backend.database.db import async_db_session


async def _run(dry_run: bool) -> None:
    async with async_db_session() as db:
        rows = list(
            (
                await db.execute(
                    sa.select(HasnNodes).where(
                        HasnNodes.ip_address.is_not(None),
                        HasnNodes.ip_address != '',
                        HasnNodes.ip_location.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        updated = skipped = 0
        for node in rows:
            location = geoip_service.lookup_location(node.ip_address)
            if location is None:
                # 私网/回环/库内查不到 → 如实留空，不伪造
                skipped += 1
                print(f'skip {node.node_id} ip={node.ip_address} （无法解析，留空）')
                continue
            updated += 1
            print(f'{"[dry-run] " if dry_run else ""}{node.node_id} ip={node.ip_address} → {location}')
            if not dry_run:
                node.ip_location = location
        if not dry_run:
            await db.commit()
        print(f'总计 {len(rows)} 行待回填：更新 {updated}，跳过 {skipped}{"（dry-run 未写库）" if dry_run else ""}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='回填 hasn_nodes.ip_location（ip2region）')
    parser.add_argument('--dry-run', action='store_true', help='只看不写')
    asyncio.run(_run(parser.parse_args().dry_run))
