"""一次性救急：把所有唤星用户对应的 new-api 用户分组补成默认分组。

背景：ensure_newapi_user 的「新建用户」分支历史上漏调 ensure_user_group（设分组只在
「已有映射」自愈分支）→ 修复前注册的新账号 new-api 分组为空字符串 → relay 按用户分组
匹配渠道时空组匹配不到任何渠道 → 首次对话即报
「No available channel for model X under group '' (distributor)」。
新建分支已补 ensure_user_group（修复后注册的账号开箱正确），本脚本把**存量**受影响账号
一次性补齐：遍历所有 active 映射，对每个 new-api 用户调 ensure_user_group（幂等：分组已对
则 no-op，空组则取整对象仅改 group 回 PUT，保留 quota）。

降级：new-api 不可达 / 无此用户 → 记录失败并继续，不中断整批。

用法（先 --dry-run 看哪些是空组）：
    PYTHONPATH=backend .venv/bin/python -m backend.scripts.reconcile_newapi_user_groups --dry-run
    PYTHONPATH=backend .venv/bin/python -m backend.scripts.reconcile_newapi_user_groups
    # 只修某手机号（尾号）匹配的用户：
    PYTHONPATH=backend .venv/bin/python -m backend.scripts.reconcile_newapi_user_groups --phone 18687200686

生产：DATABASE_URL 指向生产 huanxing 库；NEWAPI_ADMIN_* 指向生产 new-api。
"""

from __future__ import annotations

import argparse
import asyncio

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.newapi.client import NewApiError, newapi_admin_client
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.core.conf import settings
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _target_user_ids(db: AsyncSession, phone: str | None) -> list[int] | None:
    """phone 给定 → 按手机号尾号匹配的 user_id 列表；否则 None（= 全部 active 映射）。"""
    if not phone:
        return None
    from backend.app.admin.model import User

    ids = list((await db.execute(sa.select(User.id).where(User.phone.like(f'%{phone}%')))).scalars().all())
    print(f'phone %{phone}% → {len(ids)} users: {ids}')
    return ids


async def _run(*, dry_run: bool, phone: str | None) -> None:
    group = settings.NEWAPI_DEFAULT_USER_GROUP
    if not group:
        print('NEWAPI_DEFAULT_USER_GROUP 为空，未配置默认分组，终止')
        return

    async with async_db_session() as db:
        user_ids = await _target_user_ids(db, phone)
        all_mappings = list(await llm_newapi_user_mapping_dao.get_all(db))
    mappings = [
        m
        for m in all_mappings
        if m.status == 'active' and (user_ids is None or m.huanxing_user_id in user_ids)
    ]
    print(f'active 映射 {len(mappings)} 个待处理（目标分组={group!r}，dry_run={dry_run}）')

    empty: list[tuple[int, int]] = []
    fixed: list[tuple[int, int]] = []
    failed: list[tuple[int, str]] = []
    for m in mappings:
        try:
            current = await newapi_admin_client.get_user(m.newapi_user_id)
            if not current:
                failed.append((m.huanxing_user_id, 'new-api 无此用户'))
                continue
            cur_group = (current.get('group') or '').strip()
            if cur_group == group:
                continue
            empty.append((m.huanxing_user_id, m.newapi_user_id))
            print(f'  user={m.huanxing_user_id} newapi={m.newapi_user_id} 当前分组={cur_group!r} → {group!r}')
            if not dry_run:
                await newapi_admin_client.ensure_user_group(newapi_user_id=m.newapi_user_id, group=group)
                fixed.append((m.huanxing_user_id, m.newapi_user_id))
        except NewApiError as exc:
            failed.append((m.huanxing_user_id, repr(exc)))
            print(f'  user={m.huanxing_user_id} newapi={m.newapi_user_id} ❌ {exc!r}')

    if dry_run:
        print(f'(dry-run) 需补正 {len(empty)} 个，失败/不可达 {len(failed)} 个，未写任何内容')
    else:
        print(f'完成：fixed={len(fixed)} failed={len(failed)} total={len(mappings)}')
        if fixed:
            print('已补正：', fixed)
    if failed:
        print('失败：', failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='只报告不写')
    parser.add_argument('--phone', default=None, help='只修手机号（尾号）匹配的用户')
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run, phone=args.phone))


if __name__ == '__main__':
    main()
