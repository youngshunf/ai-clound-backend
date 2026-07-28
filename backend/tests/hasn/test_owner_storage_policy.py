from __future__ import annotations

import re

import pytest

from backend.common.exception import errors
from backend.app.hasn.service.owner_storage_policy import (
    CATEGORY_REGISTRY,
    LEGACY_CATEGORY_ALIASES,
    build_owner_object_key,
    owner_scope,
    resolve_category,
    resolve_owner_category,
)
from backend.plugin.s3.service.storage_service import CATEGORY_POLICY


def test_all_categories_define_complete_policy() -> None:
    assert set(CATEGORY_REGISTRY) == {
        'dm_attachment',
        'private_doc',
        'published_artifact',
        'user_upload',
        'user_avatar',
        'post_image',
        'platform_package',
        'system_preset',
        'export_staging',
    }
    for category, policy in CATEGORY_REGISTRY.items():
        assert policy.name == category
        assert policy.access in {'public', 'private'}
        assert policy.max_size_bytes > 0
        assert policy.allowed_mime_patterns
        assert policy.retention_seconds is None or policy.retention_seconds > 0


def test_existing_storage_categories_are_all_resolvable() -> None:
    for category in CATEGORY_POLICY:
        policy = resolve_category(category, allow_legacy=True)
        assert policy.name == LEGACY_CATEGORY_ALIASES.get(category, category)


def test_platform_packages_keep_public_non_billable_semantics() -> None:
    for category in ('film_engine', 'release_asset', 'speech_model'):
        policy = resolve_category(category, allow_legacy=True)
        assert policy.name == 'platform_package'
        assert policy.access == 'public'
        assert policy.billable_to_owner is False
        assert policy.sign_ttl_seconds is None
        with pytest.raises(errors.ForbiddenError, match='STORAGE_CATEGORY_FORBIDDEN'):
            resolve_owner_category(category)


def test_general_file_is_compatibility_only() -> None:
    assert resolve_category('general_file', allow_legacy=True).name == 'user_upload'
    with pytest.raises(errors.RequestError, match='STORAGE_CATEGORY_UNSUPPORTED') as exc_info:
        resolve_owner_category('general_file')
    assert exc_info.value.code == 422


def test_unknown_category_fails_closed() -> None:
    with pytest.raises(errors.RequestError, match='STORAGE_CATEGORY_UNSUPPORTED') as exc_info:
        resolve_category('unknown_category')
    assert exc_info.value.code == 422
    assert exc_info.value.data == {'category': 'unknown_category'}


@pytest.mark.parametrize(
    ('category', 'mime'),
    [
        ('user_avatar', 'image/png'),
        ('post_image', 'image/webp'),
        ('private_doc', 'application/pdf'),
        ('user_upload', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    ],
)
def test_category_accepts_expected_mime(category: str, mime: str) -> None:
    resolve_owner_category(category).assert_upload_allowed(mime=mime, size_bytes=1024)


def test_avatar_rejects_non_image_and_oversize() -> None:
    policy = resolve_owner_category('user_avatar')
    with pytest.raises(errors.RequestError, match='STORAGE_MIME_UNSUPPORTED') as mime_exc:
        policy.assert_upload_allowed(mime='application/pdf', size_bytes=1024)
    assert mime_exc.value.code == 415

    with pytest.raises(errors.RequestError, match='STORAGE_FILE_TOO_LARGE') as size_exc:
        policy.assert_upload_allowed(mime='image/png', size_bytes=policy.max_size_bytes + 1)
    assert size_exc.value.code == 413


def test_private_scope_uses_owner_identity_and_object_key_is_opaque() -> None:
    owner_hasn_id = 'h_owner_123'
    assert owner_scope(owner_hasn_id, access='private', salt=None) == owner_hasn_id
    key = build_owner_object_key(
        owner_hasn_id=owner_hasn_id,
        access='private',
        object_id='obj_01JABC',
        salt=None,
    )
    assert key == 'owners/h_owner_123/objects/obj_01JABC'
    assert not re.search(r'\.(png|pdf|zip)$', key)


def test_public_scope_is_stable_opaque_hmac() -> None:
    scope = owner_scope('h_owner_123', access='public', salt='deployment-secret')
    assert re.fullmatch(r'[0-9a-f]{16}', scope)
    assert scope == owner_scope('h_owner_123', access='public', salt='deployment-secret')
    assert scope != owner_scope('h_owner_124', access='public', salt='deployment-secret')
    key = build_owner_object_key(
        owner_hasn_id='h_owner_123',
        access='public',
        object_id='obj_01JABC',
        salt='deployment-secret',
    )
    assert 'h_owner_123' not in key
    assert key == f'owners/{scope}/objects/obj_01JABC'


def test_public_scope_without_salt_fails_closed() -> None:
    with pytest.raises(errors.ServerError, match='STORAGE_OWNER_SCOPE_SALT_MISSING'):
        owner_scope('h_owner_123', access='public', salt='')
