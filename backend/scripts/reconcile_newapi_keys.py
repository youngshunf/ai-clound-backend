"""一次性救急：把 llm_newapi_user_mapping 缓存的 API Key 与 new-api 权威重新对齐。

背景：某次部署清理了唤星库的用户/映射，但保留了 new-api 的 users/tokens。
之后聊天报 401「无效的令牌 (new_api_error)」——唤星侧持有的 key 在 new-api 已对不上。
new-api 是 token 权威源，本脚本逐行以它为准校验/对齐/重建本地映射的 newapi_token_key：
  - token 仍有效 → 取权威明文 key，与缓存不一致则就地对齐；
  - token 失效（不存在/禁用）→ 以用户身份 find-or-create 默认 relay token，写回新 token_id+key。
new-api 不可达的行保守跳过（不破坏、不造假），留待下次再跑。

前置（缺一不可，否则修了也写不进/还会复发）：
  1. 生产后端已部署到含自愈的版本（service._reconcile_mapping_key 存在）；
  2. 已执行迁移 sql/newapi/migrations/2026-06-18-widen-newapi-token-key.sql
     （newapi_token_key 列宽 = VARCHAR(128)）。

用法（先 --dry-run 看报告，确认无误再真跑）：
    # 本地 PG 15432
    PYTHONPATH=backend uv run python -m backend.scripts.reconcile_newapi_keys --dry-run
    PYTHONPATH=backend uv run python -m backend.scripts.reconcile_newapi_keys

    # 只修某个手机号尾号匹配的用户（救急定点）
    PYTHONPATH=backend uv run python -m backend.scripts.reconcile_newapi_keys --phone 18600000686

生产同样跑一次（DATABASE_URL 指向生产 huanxing 库；NEWAPI_ADMIN_* 指向生产 new-api）。
"""

from __future__ import annotations

import argparse
import asyncio

import sqlalchemy as sa

from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping
from backend.app.newapi.service import llm_newapi_user_mapping_service as svc
from backend.database.db import async_db_session


async def _run(*, dry_run: bool, phone: str | None) -> None:
    async with async_db_session() as db:
        if phone:
            # 定点：按手机号尾号匹配 sys_user → 取其映射逐行对账
            from backend.app.admin.model import User  # 延迟导入，避免无谓加载

            user_ids = list((await db.execute(sa.select(User.id).where(User.phone.like(f'%{phone}%')))).scalars().all())
            if not user_ids:
                print(f'no sys_user matched phone like %{phone}%')
                return
            rows = list(
                (
                    await db.execute(
                        sa.select(LlmNewapiUserMapping).where(LlmNewapiUserMapping.huanxing_user_id.in_(user_ids))
                    )
                )
                .scalars()
                .all()
            )
            print(f'phone %{phone}% → {len(user_ids)} users, {len(rows)} mappings')
            for m in rows:
                before_id, before_key = m.newapi_token_id, m.newapi_token_key
                new_key = await svc._reconcile_mapping_key(db, m)
                tag = (
                    'rebuilt' if m.newapi_token_id != before_id else 'aligned' if new_key != before_key else 'unchanged'
                )
                print(f'  user={m.huanxing_user_id} token_id={before_id}->{m.newapi_token_id} {tag}')
            if dry_run:
                await db.rollback()
                print('(dry-run) nothing committed')
            else:
                await db.commit()
                print('committed')
            return

        report = await svc.reconcile_all_mappings(db, dry_run=dry_run)
        print(f'report: {report}')
        print('(dry-run) nothing committed' if dry_run else 'committed')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只校验不写库')
    parser.add_argument('--phone', default=None, help='只修手机号（尾号）匹配的用户')
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run, phone=args.phone))


if __name__ == '__main__':
    main()
