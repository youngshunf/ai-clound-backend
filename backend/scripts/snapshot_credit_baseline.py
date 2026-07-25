"""积分权威重构的只读基线快照（doc94 P0）。

P0 只是**冻结**了错误余额，没有**修正**它。修正发生在 R1 的一次性 rebase；而 rebase 想安全回滚，
前提是先有一份可校验的快照。本脚本把重构前的商业与额度状态导出成带 hash 的只读快照文件。

铁律：
- **只读**。全程 SELECT，不写任何一张表，也不调用任何 NewAPI 写接口。
- **可校验**。每张表单独算 sha256，整份快照再算一次总 hash，回滚时逐表比对。
- **不臆造**。某张表不存在（已在别的窗口删掉）就如实记 `missing`，不用空列表冒充「没有数据」。

用法::

    uv run python backend/scripts/snapshot_credit_baseline.py --out ./credit-baseline

输出目录下会生成 `<表名>.jsonl` 与一份 `manifest.json`（含快照时间、逐表行数与 hash）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib

from datetime import datetime, timezone as dt_timezone
from typing import Any

from sqlalchemy import text

from backend.database.db import async_db_session

# 云端侧：商业单据与待退役的旧余额模型。
CLOUD_TABLES: tuple[str, ...] = (
    'hasn_billing.pay_order',
    'hasn_billing.pay_refund',
    'hasn_billing.user_subscription',
    'hasn_billing.user_credit_balance',
    'hasn_billing.credit_transaction',
    'hasn_billing.subscription_tier',
    'hasn_billing.credit_package',
    'hasn_billing.llm_newapi_user_mapping',
)

# NewAPI 侧的请求日志与订阅投影只能由运维用 pg_dump 直接导出：
# 云端早已解耦掉 NewAPI 第二数据库引擎，只保留管理 HTTP 通道，
# 这里如实记为「交由运维」，而不是用一份不完整的 API 抽样冒充全量快照。
NEWAPI_OPS_DUMP_TABLES: tuple[str, ...] = (
    'users',
    'user_subscriptions',
    'subscription_plans',
    'credit_operations',
    'logs',
)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _dump_table(session: Any, table: str, out_dir: pathlib.Path) -> dict[str, Any]:
    """导出单表为 jsonl，并返回 {rows, sha256} 或 {'status': 'missing'}。"""
    safe_name = table.replace('.', '__')
    target = out_dir / f'{safe_name}.jsonl'
    digest = hashlib.sha256()
    rows = 0
    try:
        result = await session.execute(text(f'SELECT * FROM {table}'))  # noqa: S608 — 表名来自本模块常量
    except Exception as exc:  # 表可能已在别的窗口删除
        return {'status': 'missing', 'error': str(exc)[:200]}

    with target.open('w', encoding='utf-8') as fp:
        for row in result.mappings():
            line = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=_json_default)
            fp.write(line + '\n')
            digest.update(line.encode('utf-8'))
            rows += 1
    return {'status': 'ok', 'rows': rows, 'sha256': digest.hexdigest(), 'file': target.name}


async def _dump_newapi_wallets(out_dir: pathlib.Path) -> dict[str, Any]:
    """经管理 HTTP 通道只读导出各映射用户的 NewAPI 钱包快照。"""
    from backend.app.newapi.client import NewApiError, newapi_admin_client

    target = out_dir / 'newapi__user_quota.jsonl'
    digest = hashlib.sha256()
    rows = 0
    unreachable = 0

    async with async_db_session() as db:
        try:
            result = await db.execute(
                text('SELECT newapi_user_id FROM hasn_billing.llm_newapi_user_mapping ORDER BY newapi_user_id')
            )
        except Exception as exc:
            return {'status': 'missing', 'error': str(exc)[:200]}
        user_ids = [int(row[0]) for row in result.all()]

    with target.open('w', encoding='utf-8') as fp:
        for start in range(0, len(user_ids), 100):
            chunk = user_ids[start : start + 100]
            try:
                batch = await newapi_admin_client.get_batch_users_quota(chunk)
            except NewApiError:
                # 如实记为不可达，绝不用 0 冒充余额——那正是本轮要消灭的假数据形态。
                unreachable += len(chunk)
                continue
            for newapi_user_id in chunk:
                info = batch.get(newapi_user_id)
                if info is None:
                    unreachable += 1
                    continue
                line = json.dumps(
                    {'newapi_user_id': newapi_user_id, **dict(info)},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=_json_default,
                )
                fp.write(line + '\n')
                digest.update(line.encode('utf-8'))
                rows += 1

    return {
        'status': 'ok',
        'rows': rows,
        'unreachable': unreachable,
        'sha256': digest.hexdigest(),
        'file': target.name,
    }


async def snapshot(out_dir: pathlib.Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    taken_at = datetime.now(dt_timezone.utc).isoformat()
    manifest: dict[str, Any] = {'taken_at': taken_at, 'cloud': {}, 'newapi': {}}

    async with async_db_session() as db:
        for table in CLOUD_TABLES:
            manifest['cloud'][table] = await _dump_table(db, table, out_dir)

    # NewAPI 侧的权威余额：经管理 HTTP 通道按映射用户批量读取（只读）。
    manifest['newapi'] = await _dump_newapi_wallets(out_dir)
    manifest['newapi_ops_dump_required'] = {
        'tables': list(NEWAPI_OPS_DUMP_TABLES),
        'note': 'NewAPI 数据库需运维单独 pg_dump；云端已无第二数据库引擎，脚本不做抽样代替全量。',
    }

    # 总 hash：把逐表 hash 按表名排序后再摘要，任何一表变动都会改变它。
    overall = hashlib.sha256()
    for name, info in sorted(manifest['cloud'].items()):
        if isinstance(info, dict) and info.get('sha256'):
            overall.update(f'cloud:{name}:{info["sha256"]}'.encode())
    newapi_hash = manifest['newapi'].get('sha256') if isinstance(manifest['newapi'], dict) else None
    if newapi_hash:
        overall.update(f'newapi:user_quota:{newapi_hash}'.encode())
    manifest['snapshot_sha256'] = overall.hexdigest()

    (out_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8'
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description='导出积分权威重构前的只读基线快照')
    parser.add_argument('--out', required=True, help='快照输出目录')
    args = parser.parse_args()

    manifest = asyncio.run(snapshot(pathlib.Path(args.out)))
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
