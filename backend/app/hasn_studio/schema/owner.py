"""统一视频引擎用户端（owner）API 请求模型（设计 doc22 §3）。

owner 面给主人在 WebUI `/apps/studio` 操作（建项目/存分镜/挑管线/派分身出片/管成品），经 daemon
`/api/v1/studio/*` 薄代理调用（铁律：WebUI 不直连云端、不直连引擎）。写类参数走 Pydantic 校验
（fail-fast），与 Agent 工具面（gateway_internal handler 收裸 dict）互补。身份恒取自 Owner JWT
（request.user.id → owner_hasn_id），**绝不从 body 读身份**。

**单一 Broker**：owner 面与 Agent 面共用同一 `studio_service` + `montage_engine_provider`，引擎是唯一耦合点。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from backend.common.schema import SchemaBase


class SaveProjectParam(SchemaBase):
    """新建/更新视频项目（传 project_id 即更新）。"""

    project_id: int | None = Field(default=None, description='传则更新该项目；不传为新建')
    title: str | None = Field(default=None, max_length=200, description='项目标题（新建必填）')
    description: str | None = Field(default=None, description='项目说明')
    default_pipeline_key: str | None = Field(default=None, max_length=80, description='项目默认管线 key')
    settings: dict[str, Any] | None = Field(default=None, description='项目设置（调性/分辨率/默认参数/品牌）')
    cover_asset_uri: str | None = Field(default=None, max_length=512, description='封面 hasn://asset/')
    bound_agent_id: str | None = Field(default=None, max_length=64, description='绑定协作分身 hasn_id')
    status: str | None = Field(default=None, max_length=16, description='状态 draft/active/archived')


class SaveStoryboardParam(SchemaBase):
    """保存分镜脚本（存进 project.settings.storyboard，避免假 asset_uri）。"""

    project_id: int = Field(description='项目 id')
    storyboard: Any = Field(default=None, description='分镜脚本（自由文本或结构化 JSON）')


class RunPipelineParam(SchemaBase):
    """跑管线出片（job 式：立即返回任务 ref，再轮询 get_render_job）。花算力出片。"""

    project_id: int = Field(description='项目 id')
    pipeline_key: str | None = Field(default=None, max_length=80, description='管线 key（缺省回落项目默认）')
    input: dict[str, Any] | None = Field(default=None, description='渲染入参（props/demo/composition_id 透传引擎）')
    work_session_id: str | None = Field(default=None, max_length=64, description='触发的工作会话 id（可选）')


class RenderParam(SchemaBase):
    """直接渲染（job 式）。props 与 demo 二选一。"""

    project_id: int = Field(description='项目 id')
    props: dict[str, Any] | None = Field(default=None, description='合成入参（与 demo 二选一）')
    demo: str | None = Field(default=None, max_length=120, description='内置 demo-props 名（与 props 二选一）')
    composition_id: str | None = Field(default=None, max_length=120, description='合成 id（可选）')
    pipeline_key: str | None = Field(default=None, max_length=80, description='管线 key（缺省回落项目默认）')
    work_session_id: str | None = Field(default=None, max_length=64, description='触发的工作会话 id（可选）')


class RunToolParam(SchemaBase):
    """调用引擎原子工具（创作段透传）。"""

    tool_name: str = Field(min_length=1, max_length=120, description='引擎原子工具名')
    inputs: dict[str, Any] | None = Field(default=None, description='工具入参')


class ExportParam(SchemaBase):
    """导出成片（换 CDN 签名下载 URL）。"""

    artifact_id: int = Field(description='成品 id')
    format: str | None = Field(default=None, max_length=20, description='导出格式（可选，当前成片即 mp4）')
