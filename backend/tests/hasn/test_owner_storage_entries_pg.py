from __future__ import annotations

import uuid

import pytest

from sqlalchemy import text

from backend.app.hasn.service.owner_storage_service import OwnerStorageService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.database.schema_names import SCHEMA_NAMES
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio


async def _chunks(payload: bytes):
    yield payload


async def _seed_owner() -> str:
    suffix = int(uuid.uuid4().hex[:10], 16)
    owner = f'h_entries_{suffix:x}'
    user_id = 975_000_000 + suffix % 10_000_000
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star, :user_id, :nickname, 'active', '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {
                'owner': owner,
                'star': f'en{user_id}',
                'user_id': user_id,
                'nickname': f'目录测试_{owner[-12:]}',
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO hasn_storage_accounts
                    (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                     quota_version, quota_valid_until, state, created_time)
                VALUES
                    (:owner, 10485760, 0, 0, 'admin_override', 'entries-test',
                     now() + interval '1 hour', 'active', now())
                """
            ),
            {'owner': owner},
        )
    return owner


async def _cleanup(owner: str) -> None:
    async with async_db_session() as db:
        objects = (
            await db.execute(
                text('SELECT storage_id, object_key FROM hasn_storage_objects WHERE owner_hasn_id = :owner'),
                {'owner': owner},
            )
        ).mappings().all()
        for obj in objects:
            await StorageService.delete_object(
                db,
                storage_id=int(obj['storage_id']),
                object_key=str(obj['object_key']),
            )
    async with async_db_session.begin() as db:
        await db.execute(
            text(f'DELETE FROM {SCHEMA_NAMES.im_table("hasn_messages")} WHERE owner_id = :owner'),
            {'owner': owner},
        )
        await db.execute(
            text('DELETE FROM hasn_knowledge.document WHERE owner_id = :owner'),
            {'owner': owner},
        )
        await db.execute(
            text('DELETE FROM hasn_knowledge.kb WHERE owner_id = :owner'),
            {'owner': owner},
        )
        await db.execute(
            text('DELETE FROM hasn_artifacts WHERE owner_hasn_id = :owner'),
            {'owner': owner},
        )
        for table in (
            'hasn_storage_entries',
            'hasn_asset_bindings',
            'hasn_assets',
            'hasn_storage_objects',
            'hasn_storage_reservations',
            'hasn_storage_jobs',
            'hasn_storage_accounts',
        ):
            await db.execute(text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'), {'owner': owner})  # noqa: S608
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


async def test_folders_listing_rename_move_and_owner_isolation() -> None:
    owner = await _seed_owner()
    other = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    try:
        first = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'entry-first'),
            declared_size=11,
            filename='Alpha 文档.txt',
            mime='text/plain',
            category='user_upload',
            source_app='entries_test',
            idempotency_key='entries-first',
        )
        second = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'entry-second'),
            declared_size=12,
            filename='Beta 文档.txt',
            mime='text/plain',
            category='private_doc',
            source_app='entries_test',
            idempotency_key='entries-second',
        )

        root = await service.create_folder(owner_hasn_id=owner, name='  Ｐｒｏｊｅｃｔ  ')
        assert root['display_name'] == 'Project'
        nested = await service.create_folder(
            owner_hasn_id=owner,
            name='资料',
            parent_entry_id=root['entry_id'],
        )
        with pytest.raises(errors.ConflictError, match='STORAGE_NAME_CONFLICT'):
            await service.create_folder(owner_hasn_id=owner, name='project')

        before = await service.entry_details(owner_hasn_id=owner, entry_id=first.entry_id)
        moved = await service.update_entry(
            owner_hasn_id=owner,
            entry_id=first.entry_id,
            version=before['version'],
            name='  重命名.txt ',
            parent_entry_id=nested['entry_id'],
        )
        assert moved['display_name'] == '重命名.txt'
        assert moved['parent_entry_id'] == nested['entry_id']
        assert moved['version'] == before['version'] + 1

        with pytest.raises(errors.ConflictError, match='STORAGE_ENTRY_VERSION_CONFLICT'):
            await service.update_entry(
                owner_hasn_id=owner,
                entry_id=first.entry_id,
                version=before['version'],
                name='旧客户端.txt',
                parent_entry_id=nested['entry_id'],
            )
        with pytest.raises(errors.ConflictError, match='STORAGE_FOLDER_CYCLE'):
            await service.update_entry(
                owner_hasn_id=owner,
                entry_id=root['entry_id'],
                version=root['version'],
                name=None,
                parent_entry_id=nested['entry_id'],
            )
        with pytest.raises(errors.NotFoundError, match='STORAGE_ENTRY_NOT_FOUND'):
            await service.entry_details(owner_hasn_id=other, entry_id=first.entry_id)

        root_page = await service.list_entries(
            owner_hasn_id=owner,
            parent_entry_id=None,
            lifecycle_status='active',
            page=1,
            page_size=20,
        )
        assert root_page['total'] == 2
        assert [item['display_name'] for item in root_page['items']] == ['Project', 'Beta 文档.txt']

        search = await service.list_entries(
            owner_hasn_id=owner,
            query='重命名',
            lifecycle_status='active',
            page=1,
            page_size=20,
        )
        assert search['total'] == 1
        assert search['items'][0]['asset_id'] == first.asset_id

        await service.trash_asset(owner_hasn_id=owner, asset_id=second.asset_id)
        trash = await service.list_entries(
            owner_hasn_id=owner,
            lifecycle_status='trashed',
            page=1,
            page_size=20,
        )
        assert trash['total'] == 1
        assert trash['items'][0]['asset_id'] == second.asset_id

        usage = await service.usage_details(owner_hasn_id=owner)
        assert usage['used_bytes'] == 23
        assert usage['category_bytes'] == {'private_doc': 12, 'user_upload': 11}
        assert usage['remaining_bytes'] == usage['quota_bytes'] - 23
    finally:
        await _cleanup(owner)
        await _cleanup(other)


async def test_cascade_delete_writes_explicit_business_tombstones() -> None:
    owner = await _seed_owner()
    service = OwnerStorageService(async_db_session)
    try:
        message_asset = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'message-tombstone'),
            declared_size=17,
            filename='消息附件.txt',
            mime='text/plain',
            category='dm_attachment',
            source_app='message_test',
            idempotency_key='message-tombstone',
        )
        knowledge_asset = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'knowledge-tombstone'),
            declared_size=19,
            filename='知识文件.txt',
            mime='text/plain',
            category='private_doc',
            source_app='knowledge_test',
            idempotency_key='knowledge-tombstone',
        )
        artifact_asset = await service.upload(
            owner_hasn_id=owner,
            chunks=_chunks(b'artifact-tombstone'),
            declared_size=18,
            filename='产物文件.txt',
            mime='text/plain',
            category='published_artifact',
            source_app='artifact_test',
            idempotency_key='artifact-tombstone',
        )

        conversation_id = str(uuid.uuid4())
        artifact_id = f'art_{uuid.uuid4().hex[:24]}'
        async with async_db_session.begin() as db:
            message_id = (
                await db.execute(
                    text(
                        f"""
                        INSERT INTO {SCHEMA_NAMES.im_table("hasn_messages")}
                            (conversation_id, conversation_seq, owner_id, from_id, from_type,
                             to_id, to_type, content_type, content, msg_type, status, priority)
                        VALUES
                            (CAST(:conversation_id AS uuid), 1, :owner, :owner, 1,
                             'h_receiver', 1, 3,
                             jsonb_build_object(
                                 'text', '请查收',
                                 'attachments', jsonb_build_array(
                                     jsonb_build_object(
                                            'uri', CAST(:asset_uri AS text),
                                         'kind', 'file',
                                         'mime', 'text/plain',
                                         'name', '消息附件.txt',
                                         'display_url', 'https://example.invalid/expired'
                                     )
                                 )
                             ),
                             'message', 1, 'normal')
                        RETURNING id
                        """
                    ),
                    {
                        'conversation_id': conversation_id,
                        'owner': owner,
                        'asset_uri': message_asset.uri,
                    },
                )
            ).scalar_one()
            kb_id = (
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_knowledge.kb
                            (owner_id, scope, name, ragflow_dataset_id, embedding_model,
                             document_count, chunk_count, status, created_time)
                        VALUES
                            (:owner, 'personal', '墓碑测试库', :dataset, 'test-model',
                             1, 1, 'active', now())
                        RETURNING id
                        """
                    ),
                    {'owner': owner, 'dataset': f'ds_{uuid.uuid4().hex}'},
                )
            ).scalar_one()
            document_id = (
                await db.execute(
                    text(
                        """
                        INSERT INTO hasn_knowledge.document
                            (kb_id, owner_id, kind, name, size_bytes, mime_type, asset_uri,
                             ragflow_document_id, parse_status, chunk_count, source, created_time)
                        VALUES
                            (:kb_id, :owner, 'file', '知识文件.txt', 19, 'text/plain', :asset_uri,
                             :ragflow_document_id, 'parsed', 1, 'ui', now())
                        RETURNING id
                        """
                    ),
                    {
                        'kb_id': kb_id,
                        'owner': owner,
                        'asset_uri': knowledge_asset.uri,
                        'ragflow_document_id': f'rf_{uuid.uuid4().hex}',
                    },
                )
            ).scalar_one()
            await db.execute(
                text(
                    """
                    INSERT INTO hasn_artifacts
                        (artifact_id, agent_hasn_id, owner_hasn_id, artifact_key,
                         artifact_kind, kind, asset_id, source_kind, action, metadata,
                         status, created_time)
                    VALUES
                        (:artifact_id, :agent, :owner, :artifact_key,
                         'file', 'file', :asset_id, 'platform_tool', 'create', '{}'::jsonb,
                         'active', now())
                    """
                ),
                {
                    'artifact_id': artifact_id,
                    'agent': f'a_{uuid.uuid4().hex[:20]}',
                    'owner': owner,
                    'artifact_key': f'asset:{artifact_asset.asset_id}',
                    'asset_id': artifact_asset.asset_id,
                },
            )
            await service.bind_asset_in_transaction(
                db,
                owner_hasn_id=owner,
                asset_id=message_asset.asset_id,
                resource_uri=f'hasn://messages/c/{conversation_id}#{message_id}',
                role='attachment',
            )
            await service.bind_asset_in_transaction(
                db,
                owner_hasn_id=owner,
                asset_id=knowledge_asset.asset_id,
                resource_uri=f'hasn://knowledge/documents/{document_id}',
                role='source',
            )
            await service.bind_asset_in_transaction(
                db,
                owner_hasn_id=owner,
                asset_id=artifact_asset.asset_id,
                resource_uri=f'hasn://artifact/{artifact_id}',
                role='source',
            )

        for asset_id in (
            message_asset.asset_id,
            knowledge_asset.asset_id,
            artifact_asset.asset_id,
        ):
            result = await service.delete_asset(
                owner_hasn_id=owner,
                asset_id=asset_id,
                cascade=True,
            )
            assert result['state'] == 'deleting'

        async with async_db_session() as db:
            message_content = (
                await db.execute(
                    text(
                        f"""
                        SELECT content
                        FROM {SCHEMA_NAMES.im_table("hasn_messages")}
                        WHERE id = :message_id
                        """
                    ),
                    {'message_id': message_id},
                )
            ).scalar_one()
            attachment = message_content['attachments'][0]
            assert attachment['tombstone'] is True
            assert attachment['tombstone_message'] == '文件已被发送方删除'
            assert 'display_url' not in attachment

            document = (
                await db.execute(
                    text(
                        """
                        SELECT parse_status, parse_error
                        FROM hasn_knowledge.document
                        WHERE id = :document_id
                        """
                    ),
                    {'document_id': document_id},
                )
            ).mappings().one()
            assert document['parse_status'] == 'failed'
            assert document['parse_error'] == '源文件缺失：主人已彻底删除云存储原件'

            artifact = (
                await db.execute(
                    text(
                        """
                        SELECT status, metadata
                        FROM hasn_artifacts
                        WHERE artifact_id = :artifact_id
                        """
                    ),
                    {'artifact_id': artifact_id},
                )
            ).mappings().one()
            assert artifact['status'] == 'missing'
            assert artifact['metadata']['tombstone_message'] == '源文件缺失'
    finally:
        await _cleanup(owner)
