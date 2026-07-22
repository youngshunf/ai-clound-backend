"""Agent 产物第一阶段唯一跨端契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ArtifactKind = Literal['resource', 'document', 'image', 'video', 'voice', 'file']
ArtifactAction = Literal['create', 'update']
ArtifactSourceKind = Literal[
    'app_write', 'platform_tool', 'runtime_file', 'agent_note', 'external_import'
]
ProjectRelationVia = Literal['participation', 'explicit_resource_link', 'linked_container']
LocalEntryKind = Literal['file', 'directory']
ArtifactAvailability = Literal['cloud', 'local_current_device', 'local_other_device', 'missing']
ArtifactSyncState = Literal['synced', 'pending', 'failed']


class ArtifactContractModel(BaseModel):
    """严格拒绝未冻结字段，避免跨端各自扩写同名 DTO。"""

    model_config = ConfigDict(extra='forbid')


class ArtifactMutation(ArtifactContractModel):
    """统一写入命令，当前态与本次参与上下文在这里同时传入但不混存。"""

    owner_hasn_id: str
    agent_hasn_id: str
    action: ArtifactAction
    source_kind: ArtifactSourceKind
    artifact_kind: ArtifactKind | None = None
    body: str | None = None
    asset_id: str | None = None
    resource_uri: str | None = None
    local_locator_key: str | None = None
    resource_kind: str | None = None
    resource_app_id: str | None = None
    origin_ref: str | None = None
    node_id: str | None = None
    local_entry_kind: LocalEntryKind | None = None
    work_session_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    message_id: int | None = None
    source_tool: str | None = None
    source_app_id: str | None = None
    dispatch_id: str | None = None
    tool_call_id: str | None = None
    source_event_id: str | None = None
    title: str | None = None
    summary: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_locator_and_kind(self) -> ArtifactMutation:
        """强制本体严格四选一，并校验定位方式对应的打开类型。"""
        locator_count = sum(
            value is not None
            for value in (self.body, self.asset_id, self.resource_uri, self.local_locator_key)
        )
        if locator_count != 1:
            raise ValueError('产物本体必须且只能提供 body、asset_id、resource_uri、local_locator_key 之一')

        if self.resource_uri is not None:
            if not self.resource_uri.startswith('hasn://') or self.resource_uri.startswith('hasn://asset/'):
                raise ValueError('应用资源必须使用非 asset 域的 hasn:// URI')
            if not self.resource_kind or not self.resource_app_id:
                raise ValueError('应用资源必须提供 resource_kind 和 resource_app_id')
            expected_kind: ArtifactKind = 'resource'
        elif self.body is not None:
            expected_kind = 'document'
        elif self.local_locator_key is not None:
            if not self.node_id or not self.local_entry_kind:
                raise ValueError('本地产物必须提供 node_id 和 local_entry_kind')
            expected_kind = 'file'
        else:
            expected_kind = self.artifact_kind or 'file'
            if expected_kind not in {'image', 'video', 'voice', 'file'}:
                raise ValueError('资产本体只允许 image、video、voice 或 file 类型')

        if self.artifact_kind is not None and self.artifact_kind != expected_kind:
            raise ValueError('artifact_kind 与产物本体定位方式不一致')
        self.artifact_kind = expected_kind
        return self


class LocalArtifactEntry(ArtifactContractModel):
    """本地产物在读模型中的最小设备定位信息。"""

    node_id: str
    entry_kind: LocalEntryKind
    device_name: str | None


class LatestContribution(ArtifactContractModel):
    """当前筛选上下文内最新的一次参与记录。"""

    contribution_id: str
    agent_hasn_id: str
    work_session_id: str | None
    project_id: str | None
    action: ArtifactAction
    source_kind: ArtifactSourceKind
    source_tool: str | None
    source_app_id: str | None
    source_link: str | None
    occurred_time: datetime


class ArtifactAgentIdentity(ArtifactContractModel):
    """读模型内嵌的分身展示身份；缺少资料时保持空值而不伪造。"""

    hasn_id: str
    display_name: str | None
    avatar_url: str | None
    profession: str | None
    owner_name: str | None


class ArtifactProjectRelation(ArtifactContractModel):
    """项目视图命中的关系类型。"""

    project_id: str
    via: ProjectRelationVia


class ArtifactListItem(ArtifactContractModel):
    """唯一产物列表项；绝不包含本地绝对路径或未签名的资产 URL。"""

    artifact_id: str
    artifact_kind: ArtifactKind
    resource_kind: str | None
    resource_app_id: str | None
    title: str | None
    summary: str | None
    body_preview: str | None
    asset_uri: str | None
    preview_url: str | None
    download_url: str | None
    resource_uri: str | None
    local_entry: LocalArtifactEntry | None
    availability: ArtifactAvailability
    allowed_actions: list[Literal['open', 'preview', 'download', 'locate']]
    sync_state: ArtifactSyncState
    latest_contribution: LatestContribution
    agent_identity: ArtifactAgentIdentity | None
    project_relation: ArtifactProjectRelation | None
    created_time: datetime
    updated_time: datetime


class ArtifactListPage(ArtifactContractModel):
    """统一产物列表分页信封。"""

    items: list[ArtifactListItem]
    total: int
    page: int
    size: int
