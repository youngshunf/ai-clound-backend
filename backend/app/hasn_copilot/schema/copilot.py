"""会议副驾用户端请求模型（Owner JWT；owner 由登录用户解析，绝不读请求体身份）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UpsertSessionRequest(BaseModel):
    """按 session_id upsert 副驾会话（session_id 由 daemon 生成，支持离线起会联网补登）。"""

    session_id: str = Field(min_length=1, max_length=64, description='工作会话 id（任务 session，UNIQUE，客户端生成）')
    bound_agent_id: str | None = Field(default=None, description='协作分身 HASN ID（owner 名下 a_*；不传则取默认）')
    title: str | None = Field(default=None, max_length=256, description='会议标题')
    scene: str | None = Field(default=None, description='场景 meeting/interview/call/lecture')
    response_mode: str | None = Field(default=None, description='应答模式 auto/manual/transcribe_only')
    status: str | None = Field(default=None, description='状态 active/ended')
    source_config: dict | None = Field(default=None, description='采集快照 JSON')
    started_time: str | None = Field(default=None, description='会议开始时间（ISO8601）')


class UpdateSessionRequest(BaseModel):
    """更新某场会话（response_mode 仅改本场，不回写 owner 默认）。"""

    bound_agent_id: str | None = Field(default=None, description='改本场协作分身（会内临时切，需校验归属）')
    title: str | None = Field(default=None, max_length=256)
    scene: str | None = Field(default=None, description='场景 meeting/interview/call/lecture')
    response_mode: str | None = Field(default=None, description='应答模式 auto/manual/transcribe_only（仅本场）')
    status: str | None = Field(default=None, description='状态 active/ended')
    source_config: dict | None = Field(default=None)


class SetProjectionRequest(BaseModel):
    """结束投影回填：记录完成卡片落在主会话哪条消息（点卡片导航回工作会话）。"""

    projection_conversation_id: str = Field(description='投影到的主 IM 会话 id（UUID）')
    projection_message_id: str = Field(description='投影的那条卡片消息 id（UUID）')
    end_session: bool = Field(default=True, description='是否同时把会话置 ended + 写 ended_time')


class UpdatePreferenceRequest(BaseModel):
    """更新 owner 副驾偏好（改默认协作分身 / 默认应答模式 / 会后自动纪要）。"""

    default_agent_id: str | None = Field(default=None, description='默认协作分身（需校验归属）')
    default_response_mode: str | None = Field(default=None, description='默认应答模式 auto/manual/transcribe_only')
    auto_summary: bool | None = Field(default=None, description='会后是否自动生成纪要')


class RebindAgentRequest(BaseModel):
    """改绑默认协作分身（§8.5.1 二次确认后）。"""

    agent_id: str = Field(min_length=1, description='新的默认协作分身 HASN ID（需校验归属）')
    also_session_id: str | None = Field(default=None, description='可选：同时改某场会话的 bound_agent_id')
