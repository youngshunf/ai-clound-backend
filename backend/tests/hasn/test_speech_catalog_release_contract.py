"""语音目录 v2 原子发布的纯契约测试。"""

from __future__ import annotations

import hashlib
import json

import pytest

from backend.app.hasn.api.v1.ci.speech_catalog import router as speech_catalog_ci_router
from backend.app.hasn.service.speech_catalog_service import (
    StagedSpeechPackageEvidence,
    build_speech_package_object_key,
    parse_release_manifest,
    validate_release_transition,
    validate_staged_release_packages,
)
from backend.common.exception import errors


def _release_document(
    *,
    package_bytes: bytes = b'PK\x03\x04real-speech-package',
    package_url: str | None = None,
) -> tuple[str, bytes]:
    """构造一份结构完整的 v2 发布信封。"""
    sha256 = hashlib.sha256(package_bytes).hexdigest()
    object_key = build_speech_package_object_key(sha256)
    url = package_url or f'https://cdn.example.com/{object_key}'
    payload = {
        'catalog_version': '2026-07-19.1',
        'issued_at': '2026-07-19T08:00:00Z',
        'models': [
            {
                'model_id': 'sensevoice-small-int8',
                'display_name': 'SenseVoice Small INT8',
                'tier': 'balanced',
                'model_version': '2024-07-17',
                'maturity': 'stable',
                'engine': 'sherpa_onnx',
                'engine_version': '1.13.4',
                'quantization': 'int8',
                'languages': ['zh', 'en'],
                'capabilities': ['stt'],
                'default_for_languages': ['zh', 'en'],
                'fallback_priority': 10,
                'experimental': False,
                'packages': [
                    {
                        'platform': {
                            'os': 'macos',
                            'arch': 'aarch64',
                            'acceleration': 'cpu',
                        },
                        'url': url,
                        'sha256': sha256,
                        'signature': 'ab' * 64,
                        'compressed_size': len(package_bytes),
                        'installed_size': len(package_bytes) * 2,
                    }
                ],
                'minimum_ram_mb': 1024,
                'recommended_ram_mb': 2048,
                'minimum_free_disk_bytes': 4096,
                'license': {
                    'name': 'Apache-2.0',
                    'url': 'https://www.apache.org/licenses/LICENSE-2.0',
                    'source': 'https://github.com/k2-fsa/sherpa-onnx',
                },
                'rollout': 100,
                'revoked': False,
                'release_sequence': 202607190001,
                'channel': 'stable',
                'expires_at': '2027-01-01T00:00:00Z',
            }
        ],
    }
    document = {
        'payload': payload,
        'key_id': 'speech-prod-2026-07-current',
        'release_sequence': 202607190001,
        'expires_at': '2027-01-01T00:00:00Z',
        'signature': 'cd' * 64,
    }
    return json.dumps(document, separators=(',', ':')), package_bytes


def test_content_addressed_object_key_uses_full_sha256() -> None:
    sha256 = hashlib.sha256(b'package-a').hexdigest()
    assert build_speech_package_object_key(sha256) == f'speech/sha256/{sha256[:2]}/{sha256}.zip'


def test_content_addressed_object_key_rejects_noncanonical_digest() -> None:
    with pytest.raises(errors.RequestError, match='sha256'):
        build_speech_package_object_key('AB' * 32)


def test_parse_v2_release_collects_signed_package_and_license_metadata() -> None:
    catalog_json, package_bytes = _release_document()

    release = parse_release_manifest(catalog_json)

    assert release.key_id == 'speech-prod-2026-07-current'
    assert release.release_sequence == 202607190001
    assert release.catalog_version == '2026-07-19.1'
    assert len(release.packages) == 1
    package = release.packages[0]
    assert package.sha256 == hashlib.sha256(package_bytes).hexdigest()
    assert package.compressed_size == len(package_bytes)
    assert package.model_id == 'sensevoice-small-int8'
    assert package.model_version == '2024-07-17'
    assert package.os == 'macos'
    assert package.arch == 'aarch64'
    assert package.acceleration == 'cpu'
    assert package.license_name == 'Apache-2.0'
    assert package.license_url.startswith('https://')
    assert package.source_url.startswith('https://')


def test_parse_v2_release_rejects_legacy_envelope() -> None:
    catalog_json, _ = _release_document()
    document = json.loads(catalog_json)
    document.pop('key_id')

    with pytest.raises(errors.RequestError, match='v2'):
        parse_release_manifest(json.dumps(document))


def test_parse_v2_release_rejects_model_metadata_conflicting_with_envelope() -> None:
    catalog_json, _ = _release_document()
    document = json.loads(catalog_json)
    document['payload']['models'][0]['release_sequence'] += 1

    with pytest.raises(errors.RequestError, match='release_sequence'):
        parse_release_manifest(json.dumps(document))


def test_parse_v2_release_rejects_missing_license_metadata() -> None:
    catalog_json, _ = _release_document()
    document = json.loads(catalog_json)
    document['payload']['models'][0]['license']['source'] = ''

    with pytest.raises(errors.RequestError, match='许可证'):
        parse_release_manifest(json.dumps(document))


def test_parse_v2_release_rejects_non_content_addressed_package_url() -> None:
    catalog_json, _ = _release_document(package_url='https://cdn.example.com/speech/models/latest.zip')

    with pytest.raises(errors.RequestError, match='内容寻址'):
        parse_release_manifest(catalog_json)


def test_parse_v2_release_rejects_declared_zero_size() -> None:
    catalog_json, _ = _release_document()
    document = json.loads(catalog_json)
    document['payload']['models'][0]['packages'][0]['compressed_size'] = 0

    with pytest.raises(errors.RequestError, match='compressed_size'):
        parse_release_manifest(json.dumps(document))


def test_release_requires_every_unique_package_to_be_staged() -> None:
    catalog_json, package_bytes = _release_document()
    release = parse_release_manifest(catalog_json)
    package = release.packages[0]

    with pytest.raises(errors.RequestError, match='尚未暂存'):
        validate_staged_release_packages(release, {})

    staged = StagedSpeechPackageEvidence(
        sha256=package.sha256,
        object_key=build_speech_package_object_key(package.sha256),
        stable_url=package.url,
        size=len(package_bytes),
    )
    assert validate_staged_release_packages(release, {package.sha256: staged}) == (staged,)


@pytest.mark.parametrize(
    ('changed_field', 'value', 'message'),
    [
        ('stable_url', 'https://cdn.example.com/speech/sha256/00/wrong.zip', 'URL'),
        ('size', 1, '大小'),
        ('object_key', 'speech/models/latest.zip', '对象 key'),
    ],
)
def test_release_rejects_staged_package_metadata_mismatch(
    changed_field: str,
    value: str | int,
    message: str,
) -> None:
    catalog_json, _ = _release_document()
    release = parse_release_manifest(catalog_json)
    package = release.packages[0]
    values: dict[str, str | int] = {
        'sha256': package.sha256,
        'object_key': build_speech_package_object_key(package.sha256),
        'stable_url': package.url,
        'size': package.compressed_size,
    }
    values[changed_field] = value
    staged = StagedSpeechPackageEvidence(**values)

    with pytest.raises(errors.RequestError, match=message):
        validate_staged_release_packages(release, {package.sha256: staged})


def test_release_transition_is_idempotent_only_for_identical_revision() -> None:
    assert (
        validate_release_transition(
            current_sequence=10,
            current_revision='same-revision',
            candidate_sequence=10,
            candidate_revision='same-revision',
        )
        == 'idempotent'
    )
    assert (
        validate_release_transition(
            current_sequence=10,
            current_revision='old-revision',
            candidate_sequence=11,
            candidate_revision='new-revision',
        )
        == 'publish'
    )

    with pytest.raises(errors.ConflictError, match='序列'):
        validate_release_transition(
            current_sequence=10,
            current_revision='old-revision',
            candidate_sequence=9,
            candidate_revision='new-revision',
        )
    with pytest.raises(errors.ConflictError, match='冲突'):
        validate_release_transition(
            current_sequence=10,
            current_revision='old-revision',
            candidate_sequence=10,
            candidate_revision='new-revision',
        )


def test_ci_router_exposes_only_two_phase_atomic_publish_contract() -> None:
    paths = {route.path for route in speech_catalog_ci_router.routes}
    assert '/packages' in paths
    assert '/releases' in paths
    assert '/publish' not in paths
