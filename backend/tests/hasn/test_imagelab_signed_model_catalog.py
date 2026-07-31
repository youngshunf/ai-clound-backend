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


# ---- 与 daemon 校验器的逐字段对齐（云端放宽即让整份目录在端上被 TrustRejected）----


def _with_model(**overrides: object) -> dict:
    document = _catalog()
    document['payload']['models']['model.rembg.u2netp'].update(overrides)
    return document


def _with_package(**overrides: object) -> dict:
    document = _catalog()
    document['payload']['models']['model.rembg.u2netp']['package'].update(overrides)
    return document


@pytest.mark.parametrize(
    'bad_key',
    [
        'runtime-model/imagelab/u2netp/2024.07/../../../secret/pkg.zip',
        'runtime-model/imagelab/u2netp/2024.07/pkg with space.zip',
        'runtime-model/imagelab/u2netp/2024.07/pkg\nnewline.zip',
    ],
)
def test_package_key_rejects_traversal_and_whitespace(bad_key: str) -> None:
    """daemon `validate_package` 拒绝 `..` 与控制字符；带空白的 key 还会让签出的 URL 404。"""
    with pytest.raises(errors.RequestError, match='包 key'):
        app_catalog_service.merge_signed_model_catalog(
            None, app_id='imagelab', document=_with_package(key=bad_key)
        )


@pytest.mark.parametrize(
    'bad_url',
    [
        'http://cdn.example.com/runtime-model/imagelab/u2netp/pkg.zip',
        'ftp://cdn.example.com/pkg.zip',
        'http://attacker.example/pkg.zip',
    ],
)
def test_package_url_requires_https_or_loopback(bad_url: str) -> None:
    """明文 http 会被 macOS 桌面端 ATS 掐断，造成随平台分叉的安装故障。"""
    with pytest.raises(errors.RequestError, match='HTTPS 或 loopback HTTP'):
        app_catalog_service.merge_signed_model_catalog(
            None, app_id='imagelab', document=_with_package(url=bad_url)
        )


def test_package_url_allows_loopback_http_for_local_dev() -> None:
    """契约测试与本地 dev 需要 loopback http，不能一刀切只留 https。"""
    merged = app_catalog_service.merge_signed_model_catalog(
        None, app_id='imagelab', document=_with_package(url='http://127.0.0.1:9000/pkg.zip')
    )
    assert merged['models']['signed_catalog']['payload']['models']['model.rembg.u2netp']['package']['url'].startswith(
        'http://127.0.0.1'
    )


@pytest.mark.parametrize('field', ['compressed_size', 'installed_size'])
def test_package_sizes_reject_values_beyond_four_gib(field: str) -> None:
    """daemon 判 MAX_PACKAGE_BYTES / MAX_MODEL_BYTES，云端只判正整数会放行多打一位的目录。"""
    oversized = app_catalog_service.MAX_SIGNED_MODEL_PACKAGE_BYTES + 1
    with pytest.raises(errors.RequestError, match='超过上限'):
        app_catalog_service.merge_signed_model_catalog(
            None, app_id='imagelab', document=_with_package(**{field: oversized})
        )


def test_model_size_rejects_values_beyond_four_gib() -> None:
    oversized = app_catalog_service.MAX_SIGNED_MODEL_PACKAGE_BYTES + 1
    document = _with_model(size=oversized)
    document['payload']['models']['model.rembg.u2netp']['package']['installed_size'] = oversized
    with pytest.raises(errors.RequestError, match='超过上限'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


@pytest.mark.parametrize('bogus_version', [True, 1.0])
def test_schema_version_rejects_bool_and_float_impostors(bogus_version: object) -> None:
    """Python 里 True == 1 且 1.0 == 1，裸相等判断会放行 JSON 的 true / 1.0。"""
    document = _catalog()
    document['payload']['schema_version'] = bogus_version
    with pytest.raises(errors.RequestError, match='schema_version'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


@pytest.mark.parametrize('bad_token', ['.', '..'])
def test_runtime_name_rejects_dot_tokens(bad_token: str) -> None:
    """`.`/`..` 能通过 token 正则，但会被拼进 object key 造成路径逃逸。"""
    document = _with_model(runtime_name=bad_token, artifact_id=f'app.model.imagelab.{bad_token}')
    with pytest.raises(errors.RequestError, match='runtime_name'):
        app_catalog_service.merge_signed_model_catalog(None, app_id='imagelab', document=document)


def test_display_name_length_is_measured_in_utf8_bytes() -> None:
    """daemon 的 `String::len()` 是字节数：50 个汉字 = 150 字节，按字符计会放行而端上拒。"""
    with pytest.raises(errors.RequestError, match='display_name'):
        app_catalog_service.merge_signed_model_catalog(
            None, app_id='imagelab', document=_with_model(display_name='模' * 50)
        )
    # 128 字节以内的中文名仍须放行。
    app_catalog_service.merge_signed_model_catalog(
        None, app_id='imagelab', document=_with_model(display_name='模' * 42)
    )


def test_filename_total_length_stays_within_daemon_limit() -> None:
    """daemon 判的是含扩展名的总长 128；词干放到 128 会让总长达到 133。"""
    with pytest.raises(errors.RequestError, match='filename'):
        app_catalog_service.merge_signed_model_catalog(
            None, app_id='imagelab', document=_with_model(filename='u' * 124 + '.onnx')
        )
    app_catalog_service.merge_signed_model_catalog(
        None, app_id='imagelab', document=_with_model(filename='u' * 123 + '.onnx')
    )


@pytest.mark.parametrize('bad_license', ['Apache 2.0', '商用授权', 'MIT OR', ''])
def test_license_must_be_an_spdx_expression(bad_license: str) -> None:
    """daemon 用 `spdx::Expression::parse` 全量解析；云端至少要挡住带空格 / 中文的误填。"""
    with pytest.raises(errors.RequestError, match='license'):
        app_catalog_service.merge_signed_model_catalog(
            None, app_id='imagelab', document=_with_model(license=bad_license)
        )


@pytest.mark.parametrize(
    'good_license',
    ['Apache-2.0', 'MIT', 'Apache-2.0 OR MIT', 'GPL-2.0-only WITH Classpath-exception-2.0'],
)
def test_license_accepts_real_spdx_expressions(good_license: str) -> None:
    """现役取值与常见组合表达式不得被误伤。"""
    app_catalog_service.merge_signed_model_catalog(
        None, app_id='imagelab', document=_with_model(license=good_license)
    )


@pytest.mark.parametrize('bad_token', ['.', '..'])
@pytest.mark.asyncio
async def test_stage_rejects_dot_runtime_name_before_reading_the_upload(
    monkeypatch: pytest.MonkeyPatch, bad_token: str
) -> None:
    """stage 在签名之前，runtime_name 是裸表单字段；`..` 必须在读上传体之前就被拒。"""
    _patch_catalog_row(monkeypatch, _FakeCatalogRow('imagelab'))
    upload = _FakeUpload()
    with pytest.raises(errors.RequestError, match='runtime_name'):
        await app_catalog_service.stage_signed_model_package(
            _FakeSession(),
            pk=7,
            runtime_name=bad_token,
            version='2024.07',
            upload=upload,
        )
    assert upload.read_calls == 0
