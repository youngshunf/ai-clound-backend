"""话题存量回填（15 §7.2）。

把 hasn_posts / hasn_articles 的 tags 字符串归一 upsert 到 hasn_topics，
写 hasn_content_topics 关联；把 hasn_follows.target_type='topic' 的字符串
归一回填为 target_hasn_id=topic_id。幂等可重复执行。

运行：
    cd huanxing-cloud-backend && uv run python backend/sql/hasn/migrations/2026-06-03-topics-backfill.py
"""

from __future__ import annotations

import asyncio

import asyncpg

from backend.app.hasn_community.service.topic_normalize import normalize_topic_name, slugify_topic
from backend.database.db import uuid4_str

DB = {'host': '127.0.0.1', 'port': 15432, 'user': 'mac', 'database': 'huanxing'}


async def _resolve_topic(conn: asyncpg.Connection, name: str) -> str | None:
    """按 lower(name) upsert 一条 active 话题，返回 topic_id；空名返回 None。"""
    norm = normalize_topic_name(name)
    if not norm:
        return None
    row = await conn.fetchrow(
        "SELECT topic_id FROM hasn_topics WHERE lower(name)=lower($1) AND status='active'", norm
    )
    if row:
        return row['topic_id']
    topic_id = f'tpc_{uuid4_str()[:12]}'
    slug = slugify_topic(norm, uuid4_str()[:8])
    # slug 唯一兜底
    if await conn.fetchval("SELECT 1 FROM hasn_topics WHERE slug=$1", slug):
        slug = f't-{uuid4_str()[:8]}'
    await conn.execute(
        """INSERT INTO hasn_topics (topic_id, name, slug, status, is_featured, is_official, created_time)
           VALUES ($1,$2,$3,'active',false,false,now())
           ON CONFLICT DO NOTHING""",
        topic_id, norm, slug,
    )
    row = await conn.fetchrow(
        "SELECT topic_id FROM hasn_topics WHERE lower(name)=lower($1) AND status='active'", norm
    )
    return row['topic_id'] if row else topic_id


async def main() -> None:
    conn = await asyncpg.connect(**DB)
    topics_created = links = follows_fixed = 0
    try:
        for content_type, table, id_col in (('post', 'hasn_posts', 'post_id'), ('article', 'hasn_articles', 'article_id')):
            rows = await conn.fetch(f"SELECT {id_col} AS cid, owner_hasn_id, tags FROM {table}")
            for r in rows:
                for tag in (r['tags'] or []):
                    topic_id = await _resolve_topic(conn, tag)
                    if not topic_id:
                        continue
                    res = await conn.execute(
                        """INSERT INTO hasn_content_topics (topic_id, content_type, content_id, owner_hasn_id, created_time)
                           VALUES ($1,$2,$3,$4,now()) ON CONFLICT DO NOTHING""",
                        topic_id, content_type, r['cid'], r['owner_hasn_id'],
                    )
                    if res.endswith('1'):
                        links += 1
        # follows: target_type='topic' 字符串 → topic_id
        frows = await conn.fetch(
            "SELECT id, target_hasn_id FROM hasn_follows WHERE target_type='topic'"
        )
        for fr in frows:
            val = fr['target_hasn_id']
            if val and val.startswith('tpc_'):
                continue  # already a topic_id
            topic_id = await _resolve_topic(conn, val)
            if topic_id:
                await conn.execute("UPDATE hasn_follows SET target_hasn_id=$1 WHERE id=$2", topic_id, fr['id'])
                follows_fixed += 1
        topics_created = await conn.fetchval("SELECT count(*) FROM hasn_topics")
        # refresh content_count
        await conn.execute(
            """UPDATE hasn_topics t SET content_count = (
                 SELECT count(*) FROM hasn_content_topics ct WHERE ct.topic_id=t.topic_id)"""
        )
    finally:
        await conn.close()
    print(f"backfill done: total_topics={topics_created} new_links={links} follows_fixed={follows_fixed}")


if __name__ == '__main__':
    asyncio.run(main())
