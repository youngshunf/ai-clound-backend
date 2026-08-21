from __future__ import annotations

import pytest

from pydantic import ValidationError

from backend.app.hasn.schema.hasn_card_message import CardMessageBody, validate_card_message_body
from backend.common.exception import errors


def _community_post_card(**overrides):
    body = {
        'schema_version': 'hasn.card/0.1',
        'title': '社区帖子',
        'description': '帖子安全摘要',
        'source': {
            'kind': 'app',
            'id': 'community',
            'display_name': '社区',
            'verified': True,
        },
        'resource': {
            'type': 'community.post',
            'id': 'post_01J',
            'app_id': 'community',
            'uri': 'hasn://app/community/posts/post_01J',
            'access': {
                'visibility': 'conversation',
                'readable_by': ['human', 'agent'],
                'required_scopes': ['community.read'],
            },
        },
        'primary_action': {
            'label': '打开帖子',
            'action_id': 'open_community_post',
            'kind': 'open_uri',
            'uri': 'hasn://app/community/posts/post_01J',
            'event': {
                'event_type': 'community.post.opened',
                'payload': {'post_id': 'post_01J'},
            },
            'style': 'primary',
        },
    }
    body.update(overrides)
    return body


def _task_card(**overrides):
    body = {
        'schema_version': 'hasn.card/0.1',
        'title': '任务「生成日报」已完成',
        'description': '已生成客户优先级和跟进建议。',
        'source': {
            'kind': 'task',
            'id': 'task_123',
            'display_name': '任务系统',
            'verified': True,
        },
        'resource': {
            'type': 'task_session',
            'id': 'sess_task_001',
            'app_id': 'tasks',
            'uri': 'hasn://webui/tasks/sessions/sess_task_001',
            'access': {
                'visibility': 'recipient',
                'readable_by': ['human'],
                'required_scopes': [],
            },
        },
        'fields': [
            {'label': '状态', 'value': 'success'},
            {'label': '完成原因', 'value': 'auto_on_final'},
        ],
        'primary_action': {
            'label': '查看任务',
            'action_id': 'open_task_session',
            'kind': 'open_uri',
            'uri': 'hasn://webui/tasks/sessions/sess_task_001',
            'event': {
                'event_type': 'task.summary.opened',
                'payload': {'session_id': 'sess_task_001'},
            },
            'style': 'primary',
        },
    }
    body.update(overrides)
    return body


def test_card_schema_accepts_community_post_and_task_cards() -> None:
    community = CardMessageBody.model_validate(_community_post_card())
    task = CardMessageBody.model_validate(_task_card())

    assert community.resource.type == 'community.post'
    assert community.primary_action is not None
    assert community.primary_action.action_id == 'open_community_post'
    assert task.resource.type == 'task_session'
    assert task.primary_action is not None
    assert task.primary_action.event is not None
    assert task.primary_action.event.event_type == 'task.summary.opened'


def test_card_schema_rejects_unsafe_uri_scheme() -> None:
    body = _community_post_card(
        resource={
            **_community_post_card()['resource'],
            'uri': 'file:///Users/owner/secrets.txt',
        }
    )

    with pytest.raises(ValidationError, match='unsupported URI scheme'):
        CardMessageBody.model_validate(body)


def test_card_schema_rejects_duplicate_action_ids() -> None:
    body = _community_post_card(
        actions=[
            {
                'label': '再次打开',
                'action_id': 'open_community_post',
                'kind': 'open_uri',
                'uri': 'hasn://app/community/posts/post_01J',
            }
        ]
    )

    with pytest.raises(ValidationError, match='duplicate action_id'):
        CardMessageBody.model_validate(body)


def test_card_schema_rejects_cleartext_token_payload() -> None:
    body = _community_post_card(
        primary_action={
            **_community_post_card()['primary_action'],
            'event': {
                'event_type': 'community.post.opened',
                'payload': {'post_id': 'post_01J', 'token': 'owner-jwt'},
            },
        }
    )

    with pytest.raises(ValidationError, match='sensitive key'):
        CardMessageBody.model_validate(body)


def test_card_schema_requires_authorization_actions_to_reference_request() -> None:
    body = _community_post_card(
        resource={
            'type': 'authorization_request',
            'id': 'authreq_01J',
            'app_id': 'crm',
            'uri': 'hasn://webui/permissions/requests/authreq_01J',
        },
        authorization_request={
            'request_id': 'authreq_01J',
            'requester': {'agent_hasn_id': 'a_sales', 'app_id': 'crm'},
            'purpose': '读取客户私密资料',
            'requested_scopes': ['crm.customer.read'],
            'resources': [{'type': 'crm.customer', 'id': 'c_1', 'display_name': 'Alice'}],
            'grant': {'mode': 'one_time'},
            'requires_owner_reauth': True,
        },
        primary_action={
            'label': '允许一次',
            'action_id': 'grant_once',
            'kind': 'grant_authorization',
            'event': {
                'event_type': 'authorization.request.granted',
                'payload': {'request_id': 'authreq_other'},
            },
            'style': 'primary',
        },
    )

    with pytest.raises(ValidationError, match='authorization request_id'):
        CardMessageBody.model_validate(body)


def test_validate_card_message_body_raises_request_error_for_server_paths() -> None:
    body = _community_post_card(primary_action={'label': '打开', 'action_id': 'open', 'kind': 'open_uri'})

    with pytest.raises(errors.RequestError, match='Card message invalid'):
        validate_card_message_body(body)
