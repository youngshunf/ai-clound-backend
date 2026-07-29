"""S9 商机项目化、版本并发与关闭必填契约测试。"""

from __future__ import annotations

import pytest

from fastapi.routing import APIRoute

from backend.app.hasn_growth.api.v1.app.growth import router as app_router
from backend.app.hasn_growth.manifest import GROWTH_AI_NATIVE_MANIFEST
from backend.app.hasn_growth.model.opportunity import Opportunity
from backend.app.hasn_growth.schema.funnel import (
    CloseDealParam,
    CreateOpportunityParam,
    UpdateStageParam,
)


def _capability(suffix: str) -> dict:
    return next(
        capability
        for capability in GROWTH_AI_NATIVE_MANIFEST['capabilities']
        if capability['tool_id'] == f'hasn_growth.{suffix.replace(".", "_")}'
    )


def test_opportunity_mutations_require_project_idempotency_and_version() -> None:
    """Agent 工具必须显式绑定项目，更新与关闭还必须携带期望版本。"""
    create = _capability('opportunity.create')
    update = _capability('opportunity.update_stage')
    close = _capability('deal.close')

    assert {'growth_project_id', 'customer_id', 'name', 'idempotency_key'} <= set(
        create['input_schema']['required']
    )
    assert {
        'growth_project_id',
        'opportunity_id',
        'stage',
        'note',
        'expected_version',
        'idempotency_key',
    } <= set(update['input_schema']['required'])
    assert {
        'growth_project_id',
        'opportunity_id',
        'result',
        'expected_version',
        'idempotency_key',
    } <= set(close['input_schema']['required'])


def test_owner_routes_are_project_scoped() -> None:
    """WebUI 所用 Owner 商机读写必须绑定权威 growth_project_id。"""
    paths = {route.path for route in app_router.routes if isinstance(route, APIRoute)}
    prefix = '/projects/{growth_project_id}/opportunities'
    assert prefix in paths
    assert f'{prefix}/{{opportunity_id}}' in paths
    assert f'{prefix}/{{opportunity_id}}/detail' in paths
    assert f'{prefix}/{{opportunity_id}}/stage' in paths
    assert f'{prefix}/{{opportunity_id}}/close' in paths


def test_stage_and_close_body_reject_missing_business_facts() -> None:
    """阶段原因、并发版本、成交金额币种和流失原因都不能靠 service 猜测。"""
    with pytest.raises(ValueError):
        UpdateStageParam.model_validate(
            {
                'stage': 'proposal',
                'expected_version': 1,
                'idempotency_key': 's9-stage-1',
            }
        )
    with pytest.raises(ValueError):
        CloseDealParam.model_validate(
            {
                'result': 'won',
                'expected_version': 1,
                'idempotency_key': 's9-close-won-1',
            }
        )
    with pytest.raises(ValueError):
        CloseDealParam.model_validate(
            {
                'result': 'lost',
                'expected_version': 1,
                'idempotency_key': 's9-close-lost-1',
            }
        )

    won = CloseDealParam.model_validate(
        {
            'result': 'won',
            'amount': 128_000,
            'currency': 'CNY',
            'expected_version': 3,
            'idempotency_key': 's9-close-won-2',
        }
    )
    lost = CloseDealParam.model_validate(
        {
            'result': 'lost',
            'lost_reason': 'budget_frozen',
            'expected_version': 4,
            'idempotency_key': 's9-close-lost-2',
        }
    )
    assert won.currency == 'CNY'
    assert lost.lost_reason == 'budget_frozen'


def test_create_cannot_bypass_close_gate_and_model_has_version() -> None:
    """创建只允许开放阶段，关闭必须经 deal.close；每条商机带单调版本。"""
    with pytest.raises(ValueError):
        CreateOpportunityParam.model_validate(
            {
                'customer_id': 1,
                'name': '绕过关闭门禁',
                'stage': 'closed_won',
                'idempotency_key': 's9-create-invalid',
            }
        )
    assert 'version' in Opportunity.__table__.columns
    assert 'review_task_id' in Opportunity.__table__.columns
