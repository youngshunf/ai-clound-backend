"""会议副驾 v5 会议结果域用户端请求模型（Owner JWT；owner 由登录用户解析，绝不读请求体身份）。

字段名与 daemon `domains/copilot/cloud.rs` 的请求体精确对齐。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateMeetingRequest(BaseModel):
    """起会建行（按 session_id upsert；started_at 是 unix 秒整数）。"""

    session_id: str = Field(min_length=1, max_length=64, description='处理工作会话 id（UNIQUE per owner）')
    agent_hasn_id: str | None = Field(default=None, description='绑定协作分身 HASN ID（owner 名下 a_*）')
    title: str | None = Field(default=None, max_length=256, description='会议标题')
    scene: str | None = Field(default=None, description='场景 meeting/interview/call/lecture')
    node_id: str | None = Field(default=None, description='采集设备节点 id')
    started_at: int | None = Field(default=None, description='会议开始时间（unix 秒，整数）')


class PatchMeetingRequest(BaseModel):
    """改会议字段（任意子集；仅 set 的字段生效，exclude_unset）。"""

    title: str | None = Field(default=None, max_length=256)
    scene: str | None = None
    status: str | None = Field(default=None, description='状态 active/ended/finalized')
    record_version: int | None = None
    participants_json: list[Any] | None = Field(default=None, description='说话人定稿快照数组')
    minutes_state: str | None = Field(default=None, description='纪要状态 none/queued/ready/failed')
    minutes_version: int | None = None
    node_id: str | None = None
    ended_at: int | None = Field(default=None, description='会议结束时间（unix 秒）')
    duration_ms: int | None = Field(default=None, description='会议时长（毫秒）')
    stats_json: dict[str, Any] | None = Field(default=None, description='会议统计对象')
    speaker_annotation_revision: str | None = None
    agent_hasn_id: str | None = Field(default=None, description='改绑协作分身（需校验归属）')


class SegmentInput(BaseModel):
    """转写定稿单段（幂等键 seq）。"""

    seq: int = Field(description='分段序号（本 record_version 内递增）')
    track: str | None = Field(default=None, description='采集轨 system/mic')
    speaker_label: str | None = Field(default=None, description='说话人标签')
    speaker_source: str | None = Field(default=None, description='说话人证据层级')
    text: str = Field(default='', description='定稿文本')
    started_ms: int = Field(default=0, description='起始时间（相对会议起点毫秒）')
    ended_ms: int | None = Field(default=None, description='结束时间（相对会议起点毫秒）')


class PutSegmentsRequest(BaseModel):
    """转写定稿幂等上推（bump record_version）。"""

    record_version: int = Field(description='本次定稿版本号')
    segments: list[SegmentInput] = Field(default_factory=list, description='定稿分段列表')


class WriteMinutesRequest(BaseModel):
    """纪要写入（幂等 version）。"""

    version: int = Field(description='纪要版本号')
    body_md: str = Field(description='纪要正文（Markdown）')
    record_view_version: int | None = Field(default=None, description='依据的转写记录视图版本')
    summary_turn_id: str | None = Field(default=None, description='生成它的工作会话轮次 id')


class AddMediaRequest(BaseModel):
    """升格媒体条目（幂等键 sha256+kind；允许 daemon 透传额外字段）。"""

    model_config = ConfigDict(extra='allow')

    kind: str = Field(description='媒体类型（audio/video/screenshot...）')
    sha256: str = Field(description='内容哈希（幂等键之一）')
    asset_uri: str | None = Field(default=None, description='hasn://asset/{id} 引用')
    media_id: str | None = Field(default=None, description='媒体条目 id（不传则云端生成）')
    track: str | None = None
    start_ms: int | None = None
    duration_ms: int | None = None
    captured_at_ms: int | None = None
    capture_mode: str | None = None
    size_bytes: int | None = None
    title: str | None = None
    state: str | None = None


class ShareMeetingRequest(BaseModel):
    """分享给联系人（permission 默认 view）。"""

    grantee_hasn_id: str = Field(min_length=1, description='被授予联系人/分身 HASN ID')
    permission: str | None = Field(default=None, description='权限档 view/edit/manage（默认 view）')


class ShareRevokeRequest(BaseModel):
    """撤销联系人访问。"""

    grantee_hasn_id: str = Field(min_length=1, description='被撤销的联系人/分身 HASN ID')
