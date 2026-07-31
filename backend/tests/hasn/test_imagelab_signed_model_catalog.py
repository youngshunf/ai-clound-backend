"""图坊 schema v1 签名模型目录的云端哑存储契约。

云端只做结构、序列与归属校验，不持有发布公钥；daemon 仍用内置 Ed25519 信任根验签。
"""

from __future__ import annotations

import copy

from collections.abc import Callable

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
def test_validation_rejects_malformed_catalog(mutate: Callable[[dict], object], expected: str) -> None:
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


def _rekeyed_catalog(*, sequence: int, dependency_id: str, package_sha256: str) -> dict:
    """同一模型（runtime_name/version 不变）换一个目录键重新发布。"""
    document = _catalog(sequence=sequence, package_sha256=package_sha256)
    release = document['payload']['models'].pop('model.rembg.u2netp')
    document['payload']['models'][dependency_id] = release
    return document


def test_merge_model_catalog_rejects_same_version_new_digest_under_a_renamed_key() -> None:
    """不可变守卫必须按 artifact_id 配对：换个目录键不得绕过同版本异摘要。"""
    published = app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=_catalog())
    with pytest.raises(errors.RequestError, match='同版本异摘要'):
        app_catalog_service.merge_signed_model_catalog(
            published,
            app_id='imagelab',
            document=_rekeyed_catalog(
                sequence=2026073199,
                dependency_id='model.u2netp',
                package_sha256='e' * 64,
            ),
        )


def test_merge_model_catalog_allows_new_digest_when_version_is_bumped() -> None:
    """版本抬了本就允许换摘要，守卫不得误伤正常发布。"""
    published = app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=_catalog())
    merged = app_catalog_service.merge_signed_model_catalog(
        published,
        app_id='imagelab',
        document=_catalog(sequence=2026073199, version='2024.08', package_sha256='e' * 64),
    )
    assert merged['models']['signed_catalog']['payload']['models']['model.rembg.u2netp']['version'] == '2024.08'


def test_merge_model_catalog_guards_migration_shaped_models_node() -> None:
    """daemon 同时接受 `models` 自身就是签名文档的迁移形态，守卫必须覆盖这条读路径。"""
    migration_shaped = {'models': _catalog(sequence=2026073150)}
    with pytest.raises(errors.RequestError, match='序列重放'):
        app_catalog_service.merge_signed_model_catalog(
            migration_shaped,
            app_id='imagelab',
            document=_catalog(sequence=2026073101),
        )


def test_merge_model_catalog_collapses_migration_shape_into_signed_catalog() -> None:
    """迁移形态发布成功后必须收敛到 signed_catalog，不得与旧文档并存。"""
    migration_shaped = {'models': _catalog(sequence=2026073150)}
    merged = app_catalog_service.merge_signed_model_catalog(
        migration_shaped,
        app_id='imagelab',
        document=_catalog(sequence=2026073151, version='2024.08'),
    )
    assert 'payload' not in merged['models']
    assert 'signature' not in merged['models']
    assert merged['models']['signed_catalog']['payload']['release_sequence'] == 2026073151


@pytest.mark.parametrize('broken_package', [None, 'not-a-dict', 42])
def test_merge_model_catalog_rejects_rather_than_500s_on_broken_previous_package(broken_package: object) -> None:
    """历史脏数据里 package 为 null/非 dict 时必须落到 400，而不是 AttributeError 500。"""
    published = app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=_catalog())
    published['models']['signed_catalog']['payload']['models']['model.rembg.u2netp']['package'] = broken_package
    with pytest.raises(errors.RequestError, match='同版本异摘要'):
        app_catalog_service.merge_signed_model_catalog(
            published,
            app_id='imagelab',
            document=_catalog(sequence=2026073199),
        )


def test_extract_current_signed_model_document_matches_daemon_read_path() -> None:
    """与 daemon `broker.rs` 的 `models.get("signed_catalog").unwrap_or(models)` 同语义。"""
    document = _catalog()
    assert app_catalog_service.extract_current_signed_model_document({'signed_catalog': document}) == document
    assert app_catalog_service.extract_current_signed_model_document(document) == document
    assert app_catalog_service.extract_current_signed_model_document({'matte_default': 'x'}) is None
    assert app_catalog_service.extract_current_signed_model_document(None) is None


class _FakeCatalogRow:
    """只承载归属校验需要的字段。"""

    def __init__(self, app_id: str, config_json: dict | None = None) -> None:
        self.app_id = app_id
        self.config_json = config_json


class _FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True

    async def flush(self) -> None:  # pragma: no cover - 归属被拒时不会走到
        raise AssertionError('归属校验失败时不应写库')


class _FakeUpload:
    """记录是否被读取，用于证明归属闸在读取上传体之前生效。"""

    def __init__(self) -> None:
        self.read_calls = 0

    async def seek(self, offset: int) -> None:
        self.read_calls += 1

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return b''


def _patch_catalog_row(monkeypatch: pytest.MonkeyPatch, row: _FakeCatalogRow) -> None:
    from backend.app.hasn.crud import crud_hasn_app_catalog

    async def fake_get(db: object, pk: int) -> _FakeCatalogRow:  # noqa: RUF029
        return row

    monkeypatch.setattr(crud_hasn_app_catalog.hasn_app_catalog_dao, 'get', fake_get)


@pytest.mark.asyncio
async def test_stage_rejects_catalog_belonging_to_another_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """选错 pk 必须在读取上传体之前就被拒，避免 GB 级包被写进别的应用命名空间。"""
    _patch_catalog_row(monkeypatch, _FakeCatalogRow('film'))
    upload = _FakeUpload()
    with pytest.raises(errors.RequestError, match='仅接受 imagelab'):
        await app_catalog_service.stage_signed_model_package(
            _FakeSession(),
            pk=7,
            runtime_name='birefnet-general',
            version='2024.07',
            upload=upload,
        )
    assert upload.read_calls == 0


@pytest.mark.asyncio
async def test_publish_rejects_catalog_belonging_to_another_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """发布面同样要有归属闸；孪生的 finance 引擎面早就有这道校验。"""
    _patch_catalog_row(monkeypatch, _FakeCatalogRow('film', {}))
    with pytest.raises(errors.RequestError, match='仅接受 imagelab'):
        await app_catalog_service.publish_signed_model_catalog(_FakeSession(), pk=7, document=_catalog())
