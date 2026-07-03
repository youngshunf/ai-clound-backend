from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, ValidationError, field_validator, model_validator

from backend.common.exception import errors
from backend.common.schema import SchemaBase

ALLOWED_CARD_URI_SCHEMES = {'hasn', 'http', 'https'}
SENSITIVE_KEYS = {'jwt', 'token', 'secret', 'authorization'}


CardSourceKind = Literal['app', 'task', 'agent', 'system', 'user', 'external']
CardResourceType = Literal[
    'task_session',
    'community.post',
    'community.article',
    'authorization_request',
    'app.resource',
    'url',
    'skill',
    'order',
    'group',
    'custom',
]
CardActionKind = Literal[
    'open_uri',
    'emit_event',
    'invoke_tool',
    'reply',
    'dismiss',
    'grant_authorization',
    'deny_authorization',
]
CardActionStyle = Literal['primary', 'default', 'danger']
CardReadableBy = Literal['human', 'agent']
CardVisibility = Literal['recipient', 'conversation', 'public']
AuthorizationGrantMode = Literal['one_time', 'time_limited', 'session', 'scope_update']
AuthorizationSensitivity = Literal['normal', 'sensitive', 'secret', 'regulated']


class CardSource(SchemaBase):
    kind: CardSourceKind
    id: str | None = None
    display_name: str
    icon_url: str | None = None
    verified: bool = False


class CardResourceAccess(SchemaBase):
    visibility: CardVisibility = 'recipient'
    readable_by: list[CardReadableBy] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)


class CardResource(SchemaBase):
    type: CardResourceType
    id: str
    app_id: str | None = None
    uri: str
    title: str | None = None
    summary: str | None = None
    access: CardResourceAccess | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('uri')
    @classmethod
    def validate_uri(cls, value: str) -> str:
        _assert_allowed_card_uri(value)
        return value

    @field_validator('metadata')
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _assert_no_sensitive_keys(value)
        return value


class CardActionEvent(SchemaBase):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator('event_type')
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('event.event_type must be non-empty')
        return value

    @field_validator('payload')
    @classmethod
    def validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _assert_no_sensitive_keys(value)
        return value


class CardAction(SchemaBase):
    label: str
    action_id: str
    kind: CardActionKind = 'open_uri'
    uri: str | None = None
    event: CardActionEvent | None = None
    style: CardActionStyle = 'default'
    requires_confirmation: bool = False

    @field_validator('label', 'action_id')
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('card action label and action_id must be non-empty')
        return value

    @field_validator('uri')
    @classmethod
    def validate_uri(cls, value: str | None) -> str | None:
        if value is not None:
            _assert_allowed_card_uri(value)
        return value

    @model_validator(mode='after')
    def validate_action_contract(self) -> CardAction:
        if self.kind == 'open_uri' and not self.uri:
            raise ValueError('kind=open_uri requires uri')
        if self.kind == 'emit_event' and self.event is None:
            raise ValueError('kind=emit_event requires event')
        return self


class CardField(SchemaBase):
    label: str
    value: str


class AuthorizationRequester(SchemaBase):
    agent_hasn_id: str
    app_id: str | None = None
    tool_id: str | None = None


class AuthorizationResource(SchemaBase):
    type: str
    id: str
    display_name: str | None = None
    sensitivity: AuthorizationSensitivity = 'normal'


class AuthorizationGrant(SchemaBase):
    mode: AuthorizationGrantMode
    expires_at: str | None = None


class CardAuthorizationRequest(SchemaBase):
    request_id: str
    requester: AuthorizationRequester
    purpose: str
    requested_scopes: list[str] = Field(default_factory=list)
    resources: list[AuthorizationResource] = Field(default_factory=list)
    grant: AuthorizationGrant
    requires_owner_reauth: bool = False

    @field_validator('request_id', 'purpose')
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('authorization_request request_id and purpose must be non-empty')
        return value


class CardMessageBody(SchemaBase):
    schema_version: str = 'hasn.card/0.1'
    title: str
    description: str | None = None
    source: CardSource
    resource: CardResource
    authorization_request: CardAuthorizationRequest | None = None
    fields: list[CardField] = Field(default_factory=list)
    primary_action: CardAction | None = None
    actions: list[CardAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('schema_version')
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != 'hasn.card/0.1':
            raise ValueError('schema_version must be hasn.card/0.1')
        return value

    @field_validator('title')
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('title must be non-empty')
        return value

    @field_validator('metadata')
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _assert_no_sensitive_keys(value)
        return value

    @model_validator(mode='after')
    def validate_card_contract(self) -> CardMessageBody:
        action_ids: list[str] = []
        if self.primary_action is not None:
            action_ids.append(self.primary_action.action_id)
        action_ids.extend(action.action_id for action in self.actions)
        if len(action_ids) != len(set(action_ids)):
            raise ValueError('duplicate action_id in card actions')

        auth_actions = [
            action
            for action in ([self.primary_action] if self.primary_action is not None else []) + self.actions
            if action.kind in {'grant_authorization', 'deny_authorization'}
        ]
        if auth_actions:
            if self.authorization_request is None:
                raise ValueError('authorization actions require authorization_request')
            request_id = self.authorization_request.request_id
            for action in auth_actions:
                payload = action.event.payload if action.event else {}
                if payload.get('request_id') != request_id:
                    raise ValueError('authorization action must reference same authorization request_id')
        return self


def validate_card_message_body(body: dict[str, Any]) -> CardMessageBody:
    try:
        return CardMessageBody.model_validate(body)
    except ValidationError as exc:
        raise errors.RequestError(msg=f'Card message invalid: {exc.errors()[0]["msg"]}') from exc


def _assert_allowed_card_uri(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in ALLOWED_CARD_URI_SCHEMES:
        raise ValueError('unsupported URI scheme')
    if parsed.scheme in {'http', 'https'} and not parsed.netloc:
        raise ValueError('external URI requires host')
    _assert_no_sensitive_uri(value)


def _assert_no_sensitive_uri(value: str) -> None:
    parsed = urlparse(value)
    for key in (parsed.query or '').replace('&', '=').split('='):
        if key.lower() in SENSITIVE_KEYS:
            raise ValueError('URI must not contain sensitive key')


def _assert_no_sensitive_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                raise ValueError('payload or metadata must not contain sensitive key')
            _assert_no_sensitive_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _assert_no_sensitive_keys(item)
