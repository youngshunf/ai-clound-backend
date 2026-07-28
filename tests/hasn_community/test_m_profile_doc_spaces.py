"""作者主页文集契约回归：真实 PostgreSQL、权限裁剪与稳定分页。"""

from __future__ import annotations

import pytest

from backend.app.hasn_community.service.doc_service import doc_service
from tests.hasn_community.conftest import seed_human


@pytest.mark.asyncio
async def test_profile_doc_spaces_hide_private_roots_and_page_without_duplicates(db):
    """他人只见公开文集；作者本人可见全部，游标翻页无重复。"""
    author = await seed_human(db, nickname='文集作者')
    viewer = await seed_human(db, nickname='主页访客')

    public_space = await doc_service.create_space(
        db,
        owner_hasn_id=author['hasn_id'],
        author_type='human',
        author_hasn_id=author['hasn_id'],
        owner_user_id=author['user_id'],
        title='公开文集',
        default_visibility='public',
    )
    private_space = await doc_service.create_space(
        db,
        owner_hasn_id=author['hasn_id'],
        author_type='human',
        author_hasn_id=author['hasn_id'],
        owner_user_id=author['user_id'],
        title='私有文集',
        default_visibility='private',
    )

    visible_to_other = await doc_service.list_by_author(
        db,
        author_hasn_id=author['hasn_id'],
        viewer_hasn_id=viewer['hasn_id'],
        limit=20,
    )
    assert [item['space_id'] for item in visible_to_other['items']] == [
        public_space['space_id']
    ]
    assert visible_to_other['next_cursor'] is None

    first = await doc_service.list_by_author(
        db,
        author_hasn_id=author['hasn_id'],
        viewer_hasn_id=author['hasn_id'],
        limit=1,
    )
    second = await doc_service.list_by_author(
        db,
        author_hasn_id=author['hasn_id'],
        viewer_hasn_id=author['hasn_id'],
        cursor=first['next_cursor'],
        limit=1,
    )
    ids = [first['items'][0]['space_id'], second['items'][0]['space_id']]
    assert set(ids) == {public_space['space_id'], private_space['space_id']}
    assert second['next_cursor'] is None
