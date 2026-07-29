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
ArtifactAvailability = Literal[
    'cloud', 'local_current_device', 'local_other_device', 'local_unavailable', 'missing'
]
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
    source_asset_uri: str | None = Field(None, pattern=r'^hasn://asset/[^/]+$')
    source_hash: str | None = Field(None, pattern=r'^[0-9a-f]{64}$')
    source_synced_at: datetime | None = None
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
    # 同一应用资源可由一个工作会话分批写入；这些计数只在本次 contribution 首次插入时累加，
    # 重放同一幂等键不再增加。调用方只能声明本次 metadata 中的非负整数键。
    accumulate_metadata_keys: list[str] = Field(default_factory=list, max_length=32)
    # 幂等键由产生 mutation 的一侧计算并原样过网，云端不得重算（设计 A12）。缺省时按确定性规则
    # 兜底并告警，绝不生成随机键——随机兜底会让 outbox 每重试一次就多一条参与记录。
    idempotency_key: str | None = None
    # 节点为同一路径算出的历史无密钥定位键；命中存量行时原地改键，避免同一文件留下两条产物
    # （设计 §4.7）。云端没有节点密钥，因而这条归并只能由节点驱动，不存在批量迁移的做法。
    supersedes_locator_key: str | None = None

    def _expected_kind_for_locator(self) -> ArtifactKind:
        """由本体定位方式反推唯一合法的打开类型（设计 §4.1）。"""
        if self.resource_uri is not None:
            if not self.resource_uri.startswith('hasn://') or self.resource_uri.startswith('hasn://asset/'):
                raise ValueError('应用资源必须使用非 asset 域的 hasn:// URI')
            if not self.resource_kind or not self.resource_app_id:
                raise ValueError('应用资源必须提供 resource_kind 和 resource_app_id')
            return 'resource'
        if self.body is not None:
            return 'document'
        if self.local_locator_key is not None:
            if not self.node_id or not self.local_entry_kind:
                raise ValueError('本地产物必须提供 node_id 和 local_entry_kind')
            # 本地对象与资产同族：按打开方式分 image/video/voice/file。目录统一走
            # file + local_entry_kind=directory，不为打开方式再造 kind（A3）。
            return self._media_kind_or_raise('本地产物只允许 image、video、voice 或 file 类型')
        return self._media_kind_or_raise('资产本体只允许 image、video、voice 或 file 类型')

    def _media_kind_or_raise(self, message: str) -> ArtifactKind:
        """媒体族缺省为 `file`；越界值直接拒绝，不静默归一。"""
        kind = self.artifact_kind or 'file'
        if kind not in {'image', 'video', 'voice', 'file'}:
            raise ValueError(message)
        return kind

    @model_validator(mode='after')
    def validate_locator_and_kind(self) -> ArtifactMutation:
        """强制本体严格四选一，并校验定位方式对应的打开类型。"""
        locator_count = sum(
            value is not None
            for value in (self.body, self.asset_id, self.resource_uri, self.local_locator_key)
        )
        if locator_count != 1:
            raise ValueError('产物本体必须且只能提供 body、asset_id、resource_uri、local_locator_key 之一')

        expected_kind = self._expected_kind_for_locator()
        if self.artifact_kind is not None and self.artifact_kind != expected_kind:
            raise ValueError('artifact_kind 与产物本体定位方式不一致')
        self.artifact_kind = expected_kind
        return self

    @model_validator(mode='after')
    def validate_source_snapshot(self) -> ArtifactMutation:
        """私有快照三元组必须全空或全非空，空值不得抹除既有快照。"""
        present = (
            self.source_asset_uri is not None,
            self.source_hash is not None,
            self.source_synced_at is not None,
        )
        if any(present) and not all(present):
            raise ValueError('source_asset_uri/source_hash/source_synced_at 必须全空或全非空')
        return self

    @model_validator(mode='after')
    def validate_accumulated_metadata(self) -> ArtifactMutation:
        """累计键必须唯一且对应非负整数，禁止把任意 JSON 当计数器相加。"""
        if len(set(self.accumulate_metadata_keys)) != len(
            self.accumulate_metadata_keys
        ):
            raise ValueError('accumulate_metadata_keys 不能重复')
        for key in self.accumulate_metadata_keys:
            if not key or len(key) > 64:
                raise ValueError('累计 metadata 键无效')
            value = self.metadata.get(key)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError('累计 metadata 值必须是非负整数')
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
    source_asset_uri: str | None
    source_hash: str | None
    source_synced_at: datetime | None
    local_entry: LocalArtifactEntry | None
    availability: ArtifactAvailability
    allowed_actions: list[Literal['open', 'preview', 'download', 'locate']]
    sync_state: ArtifactSyncState
    # A15：可空且唯一合法场景是「历史回填无法恢复任何参与事实」——此时
    # migration_lost_history=true，UI 明示「参与记录不可考」；新写入路径产生的产物永远
    # 至少有一条参与记录，出现 null 而无标记即登记链路缺陷（service 层 warn，不伪填）。
    latest_contribution: LatestContribution | None = None
    migration_lost_history: bool = False
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
