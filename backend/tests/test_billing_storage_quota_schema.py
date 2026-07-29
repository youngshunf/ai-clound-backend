"""商品目录存储权益输入契约测试。"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from backend.app.billing.schema.billing_plan import CreateBillingPlanParam


def _payload(storage_bytes: object = 10 * 1024**3) -> dict:
    return {
        'offering_key': 'llm:tier',
        'plan_key': 'free',
        'price_amount': 0,
        'price_unit': 'cny',
        'cycle': 'month',
        'quota_json': {'storage_bytes': storage_bytes},
        'trial_json': {},
        'grace_json': {},
        'status': 'active',
        'sort_order': 0,
    }


@pytest.mark.parametrize('storage_bytes', [-1, 0, 1.5, True, '10737418240'])
def test_llm_tier_rejects_non_integer_storage_quota(storage_bytes: object) -> None:
    with pytest.raises(ValidationError):
        CreateBillingPlanParam.model_validate(_payload(storage_bytes))


def test_llm_tier_requires_storage_quota() -> None:
    payload = _payload()
    payload['quota_json'] = {}

    with pytest.raises(ValidationError):
        CreateBillingPlanParam.model_validate(payload)


def test_llm_tier_accepts_exact_storage_bytes() -> None:
    obj = CreateBillingPlanParam.model_validate(_payload())

    assert obj.quota_json['storage_bytes'] == 10 * 1024**3
