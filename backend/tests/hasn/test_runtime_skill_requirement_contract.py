"""云端派发技能 requirement 的跨语言稳定契约。"""

from __future__ import annotations

import pytest

from backend.app.hasn.service.hasn_agent_runtime_dispatch_service import (
    normalize_runtime_skill_requirements,
    runtime_skill_requirements_hash,
)
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeError


def _requirement_payload() -> dict:
    return {
        'skills': [
            {
                'skill_id': 'huanxing/developer/code-review',
                'version': '1.0.0',
                'content_hash': 'sha256:code-review',
                'activation_mode': 'preload',
            }
        ],
        'bundles': [],
    }


def test_requirement_hash_matches_rust_contract() -> None:
    normalized = normalize_runtime_skill_requirements(_requirement_payload())

    assert normalized == _requirement_payload()
    assert (
        runtime_skill_requirements_hash(normalized)
        == '6d2d2d5362e9c862a0b158fc52e0c9a22dfe9e781c14bb5ab20637dc2451f14f'
    )


def test_requirement_normalization_sorts_and_deduplicates_bundle_members() -> None:
    skill = _requirement_payload()['skills'][0]
    bundle = {
        'package_id': 'huanxing/backend-dev',
        'version': '2.0.0',
        'content_hash': 'sha256:bundle',
        'bundle_slug': 'backend-dev',
        'member_skill_ids': ['huanxing/zeta', 'huanxing/alpha', 'huanxing/zeta'],
        'activation_mode': 'guided',
    }
    normalized = normalize_runtime_skill_requirements({'skills': [skill, skill], 'bundles': [bundle, bundle]})

    assert normalized['skills'] == [skill]
    assert len(normalized['bundles']) == 1
    assert normalized['bundles'][0]['member_skill_ids'] == [
        'huanxing/alpha',
        'huanxing/zeta',
    ]


@pytest.mark.parametrize(
    ('payload', 'error_code'),
    [
        (
            {
                'skills': [
                    {
                        'skill_id': 'code-review',
                        'activation_mode': 'preload',
                    }
                ],
                'bundles': [],
            },
            'runtime_skill_not_found',
        ),
        (
            {
                'skills': [],
                'bundles': [
                    {
                        'package_id': 'huanxing/backend-dev',
                        'version': '2.0.0',
                        'content_hash': 'sha256:bundle',
                        'bundle_slug': 'backend/dev',
                        'member_skill_ids': [],
                        'activation_mode': 'guided',
                    }
                ],
            },
            'runtime_skill_bundle_incomplete',
        ),
    ],
)
def test_requirement_normalization_rejects_unqualified_or_incomplete_refs(
    payload: dict,
    error_code: str,
) -> None:
    with pytest.raises(HermesRuntimeError) as captured:
        normalize_runtime_skill_requirements(payload)
    assert captured.value.error == error_code
