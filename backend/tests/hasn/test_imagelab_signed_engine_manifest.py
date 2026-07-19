"""图坊 schema v2 签名引擎 manifest 的云端哑存储契约。"""

from __future__ import annotations

import copy

import pytest

from backend.app.hasn.service import app_catalog_service
from backend.common.exception import errors


def _manifest(
    *,
    sequence: int = 2026071901,
    version: str = '0.2.0',
    sha256: str = 'a' * 64,
) -> dict:
    return {
        'payload': {
            'schema_version': 2,
            'artifact_id': 'app.engine.imagelab',
            'version': version,
            'release_sequence': sequence,
            'channel': 'stable',
            'issued_at': '2026-07-19T00:00:00Z',
            'expires_at': '2027-07-19T00:00:00Z',
            'minimum_daemon_version': '0.1.0',
            'revoked': False,
            'key_id': 'hasn-release-2026',
            'packages': {
                'macos-aarch64': {
                    'key': 'runtime-engine/imagelab/0.2.0/imagelab-macos-aarch64-0.2.0.zip',
                    'url': 'https://cdn.example.com/runtime-engine/imagelab/0.2.0/pkg.zip',
                    'sha256': sha256,
                    'compressed_size': 1024,
                    'installed_size': 4096,
                    'file_manifest_sha256': 'b' * 64,
                }
            },
        },
        'signature': 'c' * 128,
    }


def test_merge_signed_manifest_preserves_complete_document() -> None:
    document = _manifest()
    merged = app_catalog_service.merge_signed_engine_manifest(
        {'models': {'matte': ['birefnet-general']}},
        app_id='imagelab',
        document=document,
    )
    assert merged['engine'] == document
    assert merged['models'] == {'matte': ['birefnet-general']}
    assert merged['engine'] is not document


@pytest.mark.parametrize(
    ('mutation', 'message'),
    [
        (lambda doc: doc['payload'].__setitem__('unsigned_override', True), '字段'),
        (lambda doc: doc['payload'].__setitem__('artifact_id', 'app.engine.film'), 'artifact_id'),
        (
            lambda doc: doc['payload']['packages']['macos-aarch64'].__setitem__(
                'url', 'http://attacker.example/pkg.zip'
            ),
            'URL',
        ),
        (
            lambda doc: doc['payload']['packages']['macos-aarch64'].__setitem__(
                'key', 'runtime-engine/film/0.2.0/pkg.zip'
            ),
            '对象 key',
        ),
        (
            lambda doc: doc['payload']['packages']['macos-aarch64'].__setitem__(
                'file_manifest_sha256', 'deadbeef'
            ),
            'sha256',
        ),
    ],
)
def test_merge_signed_manifest_rejects_invalid_or_unbound_fields(mutation, message: str) -> None:
    document = _manifest()
    mutation(document)
    with pytest.raises(errors.RequestError, match=message):
        app_catalog_service.merge_signed_engine_manifest(
            None,
            app_id='imagelab',
            document=document,
        )


def test_merge_signed_manifest_is_idempotent_and_rejects_replay() -> None:
    current = app_catalog_service.merge_signed_engine_manifest(
        None,
        app_id='imagelab',
        document=_manifest(sequence=10),
    )
    same = app_catalog_service.merge_signed_engine_manifest(
        current,
        app_id='imagelab',
        document=_manifest(sequence=10),
    )
    assert same == current

    with pytest.raises(errors.RequestError, match='重放'):
        app_catalog_service.merge_signed_engine_manifest(
            current,
            app_id='imagelab',
            document=_manifest(sequence=9),
        )

    changed_same_sequence = _manifest(sequence=10)
    changed_same_sequence['payload']['expires_at'] = '2028-07-19T00:00:00Z'
    with pytest.raises(errors.RequestError, match='相同发布序列'):
        app_catalog_service.merge_signed_engine_manifest(
            current,
            app_id='imagelab',
            document=changed_same_sequence,
        )


def test_merge_signed_manifest_rejects_same_version_different_digest() -> None:
    current = app_catalog_service.merge_signed_engine_manifest(
        None,
        app_id='imagelab',
        document=_manifest(sequence=10, sha256='a' * 64),
    )
    with pytest.raises(errors.RequestError, match='同版本异摘要'):
        app_catalog_service.merge_signed_engine_manifest(
            current,
            app_id='imagelab',
            document=_manifest(sequence=11, sha256='d' * 64),
        )

    rotated_source = _manifest(sequence=11, sha256='a' * 64)
    rotated_source['payload']['packages']['macos-aarch64']['url'] = (
        'https://cdn2.example.com/runtime-engine/imagelab/0.2.0/pkg.zip'
    )
    merged = app_catalog_service.merge_signed_engine_manifest(
        current,
        app_id='imagelab',
        document=rotated_source,
    )
    assert merged['engine']['payload']['release_sequence'] == 11


def test_validation_does_not_mutate_caller_document() -> None:
    document = _manifest()
    original = copy.deepcopy(document)
    app_catalog_service.merge_signed_engine_manifest(
        None,
        app_id='imagelab',
        document=document,
    )
    assert document == original
