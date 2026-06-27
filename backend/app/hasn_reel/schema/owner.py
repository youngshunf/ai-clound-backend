"""短视频（reel）用户端（owner）业务 API 请求体（设计 doc29）。

owner 面 = 主人在 webui 操作短视频项目化创作（经 daemon 薄代理）。身份恒取自 Owner JWT
（owner_hasn_id 行级隔离），故请求体不含 owner_hasn_id。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SaveProjectParam(BaseModel):
    """新建或更新短视频项目（传 project_id 则更新；新建必带 title）。"""

    project_id: int | None = Field(default=None, description='项目 id（传则更新，不传则新建）')
    title: str | None = Field(default=None, max_length=200, description='项目标题（新建必填）')
    description: str | None = Field(default=None, description='项目说明')
    settings: dict[str, Any] | None = Field(default=None, description='默认创作参数（比例/时长/音色/素材源/字幕/调性）')
    cover_asset_uri: str | None = Field(default=None, max_length=512, description='封面 hasn://asset/')
    bound_agent_id: str | None = Field(default=None, max_length=64, description='项目绑定协作分身 hasn_id')
    status: str | None = Field(default=None, description='active/archived')


class CreateCreationParam(BaseModel):
    """开一次创作（统一三种发起方式）。"""

    project_id: int = Field(description='所属项目 id')
    kind: str = Field(description='发起方式 user_pipeline/agent_pipeline/agent_tools')
    title: str | None = Field(default=None, max_length=200, description='创作标题（可从 idea 派生）')
    idea: str | None = Field(default=None, description='主人需求原话')
    session_id: str | None = Field(default=None, max_length=64, description='工作会话 id（分身路径，可后置）')
    engine_task_id: str | None = Field(default=None, max_length=64, description='本地 MPT 任务 id（流水线路径，可后置）')


class SyncCreationParam(BaseModel):
    """daemon 同步创作进度/产物回写（doc29 §3 进度透明的数据层落点）。

    全部可选——daemon 推进时增量写：进行中只带 stage/progress/status，完成时带 video_ref/result_refs 等。
    """

    status: str | None = Field(default=None, description='pending/running/waiting_user/succeeded/failed')
    stage: str | None = Field(default=None, max_length=120, description='当前阶段文本')
    progress: int | None = Field(default=None, ge=0, le=100, description='进度 0-100')
    session_id: str | None = Field(default=None, max_length=64, description='回填工作会话 id')
    engine_task_id: str | None = Field(default=None, max_length=64, description='回填本地 MPT 任务 id')
    video_ref: dict[str, Any] | None = Field(default=None, description='成片引用（本地优先或上云 hasn://asset/）')
    thumbnail_asset_uri: str | None = Field(default=None, max_length=512, description='首帧/缩略图 hasn://asset/')
    duration_sec: float | None = Field(default=None, description='成片时长（秒）')
    resolution: str | None = Field(default=None, max_length=20, description='成片分辨率')
    result_refs: dict[str, Any] | None = Field(default=None, description='中间产物引用（文案/音频/字幕/素材）')
    error: str | None = Field(default=None, description='失败真实错误（透传引擎）')
