"""圈子治理动作的云端契约回归测试。

成员治理统一使用 kebab-case；内容治理只保留云端已有的通过、隐藏和删除语义。
测试连接真实 PostgreSQL，并验证 API 请求模型与 service 行为不会各自漂移。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.hasn_community.api.v1.app.community_ext import (
    ModerateContentRequest,
    ModerateMemberRequest,
)
from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.community_service import community_service
from backend.common.exception import errors
from tests.hasn_community.conftest import seed_human


def test_member_moderation_request_only_accepts_frozen_kebab_case_actions():
    """成员治理请求只接受四个冻结动作，旧下划线动作必须在 API 边界被拒绝。"""
    for action in ('approve', 'reject', 'ban'):
        assert ModerateMemberRequest(action=action).action == action
    assert ModerateMemberRequest(action='set-role', role='admin').action == 'set-role'

    with pytest.raises(ValidationError):
        ModerateMemberRequest(action='set_role', role='admin')


@pytest.mark.parametrize('action', ['remove', 'pin', 'unpin', 'reject'])
def test_content_moderation_request_rejects_unpublished_actions(action: str):
    """内容治理不接受无数据模型支撑或语义漂移的旧动作。"""
    with pytest.raises(ValidationError):
        ModerateContentRequest(content_type='post', action=action)


def test_content_moderation_request_accepts_frozen_actions():
    """内容治理请求只公开通过、隐藏、删除三种稳定语义。"""
    for action in ('approve', 'hide', 'delete'):
        request = ModerateContentRequest(content_type='article', action=action)
        assert request.action == action


@pytest.mark.asyncio
async def test_circle_service_no_longer_treats_reject_as_hidden_content(db):
    """service 也必须拒绝旧 reject 别名，避免绕过 API 后静默改变内容状态。"""
    owner = await seed_human(db, nickname='圈主')
    circle = await circle_service.create_circle(
        db,
        owner_hasn_id=owner['hasn_id'],
        owner_user_id=owner['user_id'],
        name='治理动作契约测试圈',
        join_policy='open',
        post_policy='members',
    )
    post = await community_service.create_post(
        db,
        user_id=owner['user_id'],
        hasn_id=owner['hasn_id'],
        content='待治理内容',
        circle_id=circle['circle_id'],
    )

    with pytest.raises(errors.RequestError):
        await circle_service.moderate_content(
            db,
            ident=circle['circle_id'],
            content_type='post',
            content_id=post['post_id'],
            actor_hasn_id=owner['hasn_id'],
            action='reject',
        )
