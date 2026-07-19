"""语音目录 v2 原子发布的纯契约测试。"""

from __future__ import annotations

import hashlib
import json

import pytest

from backend.app.hasn.service.speech_catalog_service import (
    build_speech_package_object_key,
    parse_release_manifest,
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
