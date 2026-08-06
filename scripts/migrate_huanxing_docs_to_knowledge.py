# -*- coding: utf-8 -*-
"""一次性数据迁移：app/huanxing 文档子域 → 知识库原生文档（D9 收编）。

设计：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/04-知识库应用与权限边界.md §7.1。

    huanxing_document_folder → hasn_knowledge.folder（树结构保持）
    huanxing_document        → hasn_knowledge.document(kind=native) + document_version(v1)
                               → 推 RAGFlow 解析（引擎失败如实落 parse_status=failed，可重试索引）
    分享链接 / 协作者 / autosave **不迁**（与网页发布/deck 分享重叠，设计 §10）

要点
- 每个有存量文档的 user 建一个「我的文档」kb（已存在同名 kb 则复用——幂等）；
- user → hasn_id 经 public.hasn_humans 映射；无 HASN 身份的用户**如实跳过并打印**（零静默）；
- 旧表用原生 SQL 读取（不依赖已退役的 ORM model）；软删行（deleted_at 非空）不迁。

用法：
    cd huanxing-cloud-backend && DATABASE_PORT=15432 uv run python scripts/migrate_huanxing_docs_to_knowledge.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa  # noqa: E402

from backend.app.hasn_knowledge.service.knowledge_service import knowledge_service  # noqa: E402
from backend.database.db import async_db_session  # noqa: E402

MIGRATED_KB_NAME = '我的文档'


async def main() -> None:
    async with async_db_session.begin() as db:
        users = (
            await db.execute(
                sa.text(
                    'SELECT DISTINCT d.user_id FROM huanxing_document d WHERE d.deleted_at IS NULL'
                )
            )
        ).scalars().all()
        print(f'待迁移用户数：{len(users)}')
        total_docs = 0
        for user_id in users:
            hasn_id = (
                await db.execute(
                    sa.text('SELECT hasn_id FROM hasn_humans WHERE user_id = :uid LIMIT 1'), {'uid': user_id}
                )
            ).scalar_one_or_none()
            if not hasn_id:
                print(f'⚠️ user {user_id} 无 HASN 身份，跳过（文档保留在旧表，须人工处置）')
                continue

            # 幂等：已存在「我的文档」库则复用
            existing = next(
                (kb for kb in await knowledge_service.list_kbs(db, hasn_id) if kb['name'] == MIGRATED_KB_NAME),
                None,
            )
            if existing:
                kb = existing
                print(f'user {user_id} 复用既有库 kb_id={kb["id"]}')
            else:
                kb = await knowledge_service.create_kb(
                    db, hasn_id, name=MIGRATED_KB_NAME, description='自原文档模块迁移（D9 收编）'
                )
                print(f'user {user_id} 建库 kb_id={kb["id"]}')

            # 目录树（按 parent 拓扑序插入；旧->新 id 映射）
            folders = (
                await db.execute(
                    sa.text(
                        'SELECT id, name, parent_id, sort_order FROM huanxing_document_folder '
                        'WHERE user_id = :uid AND deleted_at IS NULL ORDER BY id'
                    ),
                    {'uid': user_id},
                )
            ).mappings().all()
            id_map: dict[int, int] = {}
            remaining = list(folders)
            existing_folders = await knowledge_service.list_folders(db, hasn_id, kb['id'])
            for _ in range(len(remaining) + 1):
                progressed = False
                for f in list(remaining):
                    parent_old = f['parent_id']
                    if parent_old is not None and parent_old not in id_map:
                        continue
                    parent_new = id_map.get(parent_old) if parent_old is not None else None
                    # 幂等：同层同名已存在则复用
                    dup = next(
                        (
                            x
                            for x in existing_folders
                            if x['name'] == f['name'] and x['parent_id'] == parent_new
                        ),
                        None,
                    )
                    if dup:
                        id_map[f['id']] = dup['id']
                    else:
                        created = await knowledge_service.create_folder(
                            db, hasn_id, kb['id'], name=f['name'], parent_id=parent_new
                        )
                        id_map[f['id']] = created['id']
                        existing_folders.append(created)
                    remaining.remove(f)
                    progressed = True
                if not remaining or not progressed:
                    break
            if remaining:
                print(f'⚠️ user {user_id} 有 {len(remaining)} 个目录父链断裂，挂到库根')
                for f in remaining:
                    created = await knowledge_service.create_folder(db, hasn_id, kb['id'], name=f['name'])
                    id_map[f['id']] = created['id']

            # 文档（kind=native；推引擎解析）
            docs = (
                await db.execute(
                    sa.text(
                        'SELECT id, title, content, folder_id, created_by, agent_id FROM huanxing_document '
                        'WHERE user_id = :uid AND deleted_at IS NULL ORDER BY id'
                    ),
                    {'uid': user_id},
                )
            ).mappings().all()
            migrated_names = {
                d['name'] for d in await knowledge_service.list_documents(db, hasn_id, kb['id'])
            }
            for d in docs:
                title = (d['title'] or '无标题文档').strip() or '无标题文档'
                if title in migrated_names:
                    print(f'  跳过已迁移：{title[:30]}')
                    continue
                is_agent = (d['created_by'] or '') == 'agent' and d['agent_id']
                doc = await knowledge_service.create_native_document(
                    db,
                    hasn_id,
                    kb['id'],
                    title=title,
                    content=d['content'] or '',
                    folder_id=id_map.get(d['folder_id']) if d['folder_id'] else None,
                    source='agent' if is_agent else 'ui',
                    agent_hasn_id=d['agent_id'] if is_agent else None,
                )
                total_docs += 1
                print(f'  迁移 doc {d["id"]} → knowledge doc {doc["id"]}（{doc["parse_status"]}）：{title[:30]}')
        print(f'✅ 迁移完成：{total_docs} 篇文档')


if __name__ == '__main__':
    asyncio.run(main())
