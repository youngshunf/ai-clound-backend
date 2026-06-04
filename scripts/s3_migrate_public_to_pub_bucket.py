"""一次性迁移：把存量公开资产从私有桶(hasn-cdn) 拷到公共桶(hasn-pub-cdn) 并回写 URL。

背景（07 D1/D3 公私分桶）：公开图片应落公共桶 → CDN 直读不签名。但历史公开图片仍在
私有桶，前端展示要来回请求签名（与"不来回签名"的设计相悖）。本脚本把 5 个**权威公开图列**
里的 hasn-cdn URL 对应对象跨桶复制到公共桶，并把 URL 改写为 hasn-pub-cdn。

关键前提：私有桶(id=1)与公共桶(id=6) **prefix 相同(huanxing)**，故对象 key 不变、URL 仅换 host，
迁移是"同 key 跨桶复制 + host 改写"，可逆、易核对。

幂等：
- URL 已在 hasn-pub-cdn 的跳过；
- 目标对象已存在的只改 URL、不重复拷贝；
- 源对象缺失的**不改 URL**（避免指向不存在对象），如实报告。

零 fake：缺桶/缺对象/读写失败直接报告，不静默改 URL。

**不迁移**（dry-run 会列出，证明未遗漏，而非静默截断）：
- hasn_messages.content     消息历史（不可变，且可能是私信私有图，应保持私有签名）
- hasn_notifications.data/source  通知快照（瞬态，会过期）
- hasn_sync_events/sync_inbox_events.payload  同步事件（瞬态事件日志）
- sys_opera_log.args        审计日志（历史，改写审计=篡改）
- s3_storage.cdn_domain     存储配置本身（私有桶的 CDN 域名，非资产 URL）

用法：
    python scripts/s3_migrate_public_to_pub_bucket.py            # dry-run（默认，只报告）
    python scripts/s3_migrate_public_to_pub_bucket.py --apply    # 真正拷贝 + 回写
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import sys

from dataclasses import dataclass, field

sys.path.insert(0, '.')

PRIVATE_HOST = 'hasn-cdn.dcfuture.cn'
PUBLIC_HOST = 'hasn-pub-cdn.dcfuture.cn'

# (表, 主键列, URL 列) —— 权威公开图列，整格即单个 URL。
TARGETS: list[tuple[str, str, str]] = [
    ('hasn_agents', 'id', 'avatar'),
    ('hasn_articles', 'id', 'cover_url'),
    ('hasn_humans', 'id', 'avatar'),
    ('marketplace_template', 'id', 'icon_url'),
    ('sys_user', 'id', 'avatar'),
]

# 含 hasn-cdn 但**不迁移**的列（报告用，证明未遗漏）。
SKIP_COLUMNS: list[tuple[str, str, str]] = [
    ('hasn_messages', 'content', '消息历史(不可变/可能是私信私有图)'),
    ('hasn_notifications', 'data', '通知快照(瞬态)'),
    ('hasn_notifications', 'source', '通知快照(瞬态)'),
    ('hasn_sync_events', 'payload', '同步事件(瞬态)'),
    ('hasn_sync_inbox_events', 'payload', '同步事件(瞬态)'),
    ('sys_opera_log', 'args', '审计日志(历史)'),
    ('s3_storage', 'cdn_domain', '存储配置(私有桶 CDN 域名，非资产URL)'),
]


@dataclass
class Stats:
    rewritten: int = 0
    copied: int = 0
    dst_existed: int = 0
    src_missing: list[str] = field(default_factory=list)
    already_public: int = 0
    errors: list[str] = field(default_factory=list)


def _content_type(key: str) -> str:
    ctype, _ = mimetypes.guess_type(key)
    return ctype or 'application/octet-stream'


async def main(apply: bool) -> int:
    from sqlalchemy import text

    from backend.common.exception import errors
    from backend.database.db import async_db_session
    from backend.plugin.s3.crud.storage import s3_storage_dao
    from backend.plugin.s3.utils.file_ops import (
        build_object_url,
        get_operator_for_storage,
        object_key_from_url,
        write_bytes,
    )

    mode = 'APPLY（真实拷贝+回写）' if apply else 'DRY-RUN（只报告，不写）'
    print(f'==== S3 公开资产存量迁移 [{mode}] ====\n')

    async with async_db_session() as db:
        storages = await s3_storage_dao.get_all(db)
        private = next((s for s in storages if getattr(s, 'access', '') == 'private'), None)
        public = next((s for s in storages if getattr(s, 'access', '') == 'public'), None)
        if not private or not public:
            print('✗ 缺少 access=private 或 access=public 的存储行，无法迁移。')
            print(f'  现有: {[(s.id, s.name, getattr(s, "access", "?")) for s in storages]}')
            return 2
        print(f'私有桶 id={private.id} {private.name} bucket={private.bucket} prefix={private.prefix} cdn={private.cdn_domain}')
        print(f'公共桶 id={public.id} {public.name} bucket={public.bucket} prefix={public.prefix} cdn={public.cdn_domain}\n')

        priv_op = get_operator_for_storage(private)
        pub_op = get_operator_for_storage(public)
        stats = Stats()

        for table, pk_col, url_col in TARGETS:
            result = await db.execute(
                text(f'SELECT "{pk_col}" AS pk, "{url_col}" AS url FROM "{table}" WHERE "{url_col}" LIKE :pat'),
                {'pat': f'%{PRIVATE_HOST}%'},
            )
            recs = result.fetchall()
            if not recs:
                continue
            print(f'--- {table}.{url_col}: {len(recs)} 行 ---')
            for pk, url in recs:
                if PUBLIC_HOST in url:
                    stats.already_public += 1
                    continue
                try:
                    key = object_key_from_url(private, url)
                except errors.RequestError as e:
                    stats.errors.append(f'{table}#{pk}: URL 不属于私有桶 {url} ({e})')
                    print(f'  ✗ #{pk} URL 不属私有桶: {url}')
                    continue

                # 源对象存在性
                try:
                    await priv_op.stat(key)
                except Exception:  # noqa: BLE001 opendal NotFound 等
                    stats.src_missing.append(f'{table}#{pk}: {key}  ({url})')
                    print(f'  ⚠ #{pk} 源对象缺失，跳过改写: {key}')
                    continue

                new_url = build_object_url(public, key)
                # 目标对象存在性
                dst_exists = True
                try:
                    await pub_op.stat(key)
                except Exception:  # noqa: BLE001
                    dst_exists = False

                action = '目标已存在→仅改URL' if dst_exists else '拷贝→公共桶 + 改URL'
                print(f'  • #{pk} {action}\n      {url}\n   -> {new_url}')

                if not apply:
                    continue

                try:
                    if not dst_exists:
                        data = await priv_op.read(key)
                        body = data if isinstance(data, (bytes, bytearray)) else bytes(data)
                        await write_bytes(public, key, bytes(body), _content_type(key))
                        stats.copied += 1
                    else:
                        stats.dst_existed += 1
                    await db.execute(
                        text(
                            f'UPDATE "{table}" SET "{url_col}" = :new '
                            f'WHERE "{pk_col}" = :pk AND "{url_col}" = :old'
                        ),
                        {'new': new_url, 'pk': pk, 'old': url},
                    )
                    stats.rewritten += 1
                except Exception as e:  # noqa: BLE001
                    stats.errors.append(f'{table}#{pk}: {type(e).__name__}: {e}')
                    print(f'  ✗ #{pk} 迁移失败: {e}')

        if apply:
            await db.commit()

        # 跳过列报告（证明未遗漏）
        print('\n--- 含 hasn-cdn 但**不迁移**的列（设计上保留）---')
        for table, col, reason in SKIP_COLUMNS:
            try:
                r = await db.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE "{col}"::text LIKE :pat'),
                    {'pat': f'%{PRIVATE_HOST}%'},
                )
                n = r.scalar()
            except Exception:  # noqa: BLE001
                n = '?'
            print(f'  · {table}.{col}: {n} 行 —— {reason}')

        print('\n==== 汇总 ====')
        print(f'  改写 URL : {stats.rewritten}')
        print(f'  跨桶拷贝 : {stats.copied}')
        print(f'  目标已存在(仅改URL): {stats.dst_existed}')
        print(f'  已是公共(跳过): {stats.already_public}')
        print(f'  源对象缺失(未改): {len(stats.src_missing)}')
        for m in stats.src_missing:
            print(f'      - {m}')
        print(f'  错误: {len(stats.errors)}')
        for e in stats.errors:
            print(f'      - {e}')
        if not apply:
            print('\n（DRY-RUN：未做任何写入。确认无误后加 --apply 执行。）')
        return 1 if stats.errors else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='真正拷贝+回写（缺省 dry-run）')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
