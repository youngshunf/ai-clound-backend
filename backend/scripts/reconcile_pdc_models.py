"""PDC 模型名对账（P3 上线前必跑）。

## 为什么必须先跑

`update_config` 一旦开始校验模型名，**存量 PDC 里任何一个写错的名字都会让运营下次保存直接被拒**
——哪怕他这次改的是别的字段。所以开闸前先跑一遍对账，把不匹配的列成清单给运营修完，
再让校验生效（设计 §8 已点名的风险）。

## 用法

    # 只对账，不改任何东西（默认）
    .venv/bin/python -m backend.scripts.reconcile_pdc_models

    # 顺带先从 new-api 同步一轮注册表再对账（首次运行建议带上）
    .venv/bin/python -m backend.scripts.reconcile_pdc_models --sync

退出码：0 = 干净（可以开闸）；1 = 有不匹配项（照清单修完再来）；2 = 注册表是空的（先同步）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from backend.app.hasn.service.pdc_model_validation_service import (
    collect_configured_models,
    build_rejections,
    pdc_model_validation_service,
)
from backend.app.hasn.service.platform_default_config_service import platform_default_config_service
from backend.database.db import async_db_session


async def _reconcile(sync_first: bool) -> int:
    async with async_db_session() as db:
        if sync_first:
            from backend.app.hasn.service.model_registry_sync_service import model_registry_sync_service

            report = await model_registry_sync_service.sync(db)
            await db.commit()
            print(f'[同步] 网关 {report.upstream_total} 个模型：新增 {report.created}、更新 {report.updated}、'
                  f'消失 {report.missing}、待标注 {report.unclassified}')

        active, missing = await pdc_model_validation_service.load_registry_names(db)
        if not active and not missing:
            print('[对账] 模型注册表是空的——先跑一次同步（加 --sync）再来对账。')
            return 2

        config, revision = await platform_default_config_service.get_effective_config(db)
        configured = collect_configured_models(config)
        rejections = build_rejections(configured, active, missing)

        print(f'[对账] PDC revision={revision}，配置里共 {len(configured)} 处模型名；'
              f'注册表 active={len(active)} missing={len(missing)}')
        for path, name in configured:
            mark = 'OK ' if name in active else ('MISSING' if name in missing else 'UNKNOWN')
            print(f'  {mark:>8}  {path} = {name}')

        if not rejections:
            print('[对账] 全部匹配，可以开闸。')
            return 0

        print(f'\n[对账] {len(rejections)} 处不匹配，需运营修完再开闸：')
        print(json.dumps(rejections, ensure_ascii=False, indent=2))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description='PDC 模型名对账（P3 上线前必跑）')
    parser.add_argument('--sync', action='store_true', help='对账前先从 new-api 同步一轮注册表')
    args = parser.parse_args()
    return asyncio.run(_reconcile(args.sync))


if __name__ == '__main__':
    sys.exit(main())
