"""图坊 schema v1 签名模型目录的云端哑存储契约。

云端只做结构、序列与归属校验，不持有发布公钥；daemon 仍用内置 Ed25519 信任根验签。
"""

from __future__ import annotations

import copy

import pytest
from fastapi.routing import APIRoute

from backend.app.hasn.service import app_catalog_service
from backend.common.exception import errors


def _catalog(
    *,
    sequence: int = 2026073101,
    version: str = '2024.07',
    package_sha256: str = 'a' * 64,
) -> dict:
    return {
        'payload': {
            'schema_version': 1,
            'catalog_id': 'imagelab-models',
            'release_sequence': sequence,
            'channel': 'stable',
            'issued_at': '2026-07-31T00:00:00Z',
            'expires_at': '2027-07-31T00:00:00Z',
            'minimum_daemon_version': '0.1.0',
            'key_id': 'hasn-release-2026',
            'models': {
                'model.rembg.u2netp': {
                    'runtime_name': 'u2netp',
                    'artifact_id': 'app.model.imagelab.u2netp',
                    'display_name': 'U²-Net 轻量抠图',
                    'purposes': ['remove_background'],
                    'license': 'Apache-2.0',
                    'version': version,
                    'filename': 'u2netp.onnx',
                    'size': 4574861,
                    'sha256': 'b' * 64,
                    'revoked': False,
                    'package': {
                        'key': f'runtime-model/imagelab/u2netp/{version}/0123456789abcdef-u2netp.zip',
                        'url': 'https://cdn.example.com/runtime-model/imagelab/u2netp/pkg.zip',
                        'sha256': package_sha256,
                        'compressed_size': 4200000,
                        'installed_size': 4574861,
                    },
                }
            },
        },
        'signature': 'c' * 128,
    }


def test_merge_model_catalog_preserves_document_and_other_config() -> None:
    document = _catalog()
    merged = app_catalog_service.merge_signed_model_catalog(
        {'engine': {'payload': {'schema_version': 2}}},
        app_id='imagelab',
        document=document,
    )
    assert merged['models'] == {'signed_catalog': document}
    assert merged['engine'] == {'payload': {'schema_version': 2}}
    assert merged['models']['signed_catalog'] is not document


def test_merge_model_catalog_keeps_other_keys_under_models_node() -> None:
    merged = app_catalog_service.merge_signed_model_catalog(
        {'models': {'matte_default': 'birefnet-general'}},
        app_id='imagelab',
        document=_catalog(),
    )
    assert merged['models']['matte_default'] == 'birefnet-general'
    assert merged['models']['signed_catalog']['payload']['catalog_id'] == 'imagelab-models'


def test_merge_model_catalog_is_idempotent_within_same_sequence() -> None:
    document = _catalog()
    first = app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)
    second = app_catalog_service.merge_signed_model_catalog(first, app_id='imagelab', document=_catalog())
    assert second == first


def test_merge_model_catalog_rejects_sequence_replay() -> None:
    published = app_catalog_service.merge_signed_model_catalog(
        None,
        app_id='imagelab',
        document=_catalog(sequence=2026073102),
    )
    with pytest.raises(errors.RequestError, match='序列重放'):
        app_catalog_service.merge_signed_model_catalog(
            published,
            app_id='imagelab',
            document=_catalog(sequence=2026073101),
        )


def test_merge_model_catalog_rejects_same_sequence_with_different_body() -> None:
    published = app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=_catalog())
    with pytest.raises(errors.RequestError, match='内容不一致'):
        app_catalog_service.merge_signed_model_catalog(
            published,
            app_id='imagelab',
            document=_catalog(package_sha256='d' * 64),
        )


def test_merge_model_catalog_rejects_same_version_with_new_digest() -> None:
    published = app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=_catalog())
    with pytest.raises(errors.RequestError, match='同版本异摘要'):
        app_catalog_service.merge_signed_model_catalog(
            published,
            app_id='imagelab',
            document=_catalog(sequence=2026073199, package_sha256='e' * 64),
        )


@pytest.mark.parametrize(
    ('mutate', 'expected'),
    [
        (lambda doc: doc['payload'].update(schema_version=2), 'schema_version'),
        (lambda doc: doc['payload'].update(catalog_id='other'), 'catalog_id'),
        (lambda doc: doc['payload'].update(release_sequence=0), 'release_sequence'),
        (lambda doc: doc['payload'].update(expires_at='2026-07-31T00:00:00Z'), 'expires_at'),
        (lambda doc: doc['payload'].update(models={}), 'models 不能为空'),
        (lambda doc: doc['payload'].update(minimum_daemon_version='一点零'), 'minimum_daemon_version'),
        (lambda doc: doc.update(signature='zz'), 'signature'),
        (lambda doc: doc['payload'].update(unexpected=1), 'payload '),
    ],
)
def test_validation_rejects_malformed_catalog(mutate, expected: str) -> None:
    document = _catalog()
    mutate(document)
    with pytest.raises(errors.RequestError, match=expected):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_validation_rejects_artifact_id_not_derived_from_runtime_name() -> None:
    document = _catalog()
    document['payload']['models']['model.rembg.u2netp']['artifact_id'] = 'app.model.imagelab.other'
    with pytest.raises(errors.RequestError, match='artifact_id'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_validation_rejects_installed_size_mismatching_model_size() -> None:
    document = _catalog()
    document['payload']['models']['model.rembg.u2netp']['package']['installed_size'] = 1
    with pytest.raises(errors.RequestError, match='installed_size'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_validation_rejects_package_key_outside_model_namespace() -> None:
    document = _catalog()
    document['payload']['models']['model.rembg.u2netp']['package']['key'] = 'runtime-engine/imagelab/x.zip'
    with pytest.raises(errors.RequestError, match='runtime-model/'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_validation_rejects_filename_with_path_or_wrong_extension() -> None:
    for filename in ('sub/u2netp.onnx', 'u2netp.bin', '../u2netp.onnx'):
        document = _catalog()
        document['payload']['models']['model.rembg.u2netp']['filename'] = filename
        with pytest.raises(errors.RequestError, match='filename'):
            app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_validation_rejects_dependency_id_outside_model_namespace() -> None:
    document = _catalog()
    document['payload']['models']['rembg.u2netp'] = document['payload']['models'].pop('model.rembg.u2netp')
    with pytest.raises(errors.RequestError, match='依赖 ID'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_validation_does_not_mutate_caller_document() -> None:
    document = _catalog()
    original = copy.deepcopy(document)
    app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)
    assert document == original


def test_admin_router_exposes_two_phase_model_publish_endpoints() -> None:
    from backend.app.hasn.api.v1.admin.hasn_app_catalog import router

    routes = {(route.path, route.name) for route in router.routes if isinstance(route, APIRoute)}
    assert ('/{pk}/model-package-stage', 'admin_stage_signed_model_package') in routes
    assert ('/{pk}/model-catalog', 'admin_publish_signed_model_catalog') in routes
