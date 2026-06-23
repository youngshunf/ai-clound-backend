from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class StudioRenderJobSchemaBase(SchemaBase):
    """视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）基础模型"""
    project_id: int = Field(description='所属项目 id（FK→studio_project）')
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: str | None = Field(None, description='发起分身 hasn_id')
    pipeline_key: str = Field(description='跑哪条管线 key（run_pipeline，来自引擎 pipeline_defs）')
    input: dict = Field(description='入参快照 jsonb（脚本/素材引用/参数，不回指项目当前值）')
    engine_job_id: str | None = Field(None, description='引擎侧 job 标识（云端轮询/回流用）')
    status: str = Field(description='状态 (queued:排队中:blue/running:执行中:processing/succeeded:成功:green/failed:失败:red/canceled:已取消:default)')
    progress: int = Field(description='进度 0-100')
    stage: str | None = Field(None, description='当前阶段文本（生成第3镜/配音/合成，喂 CRX-1 进度抽屉）')
    cost: dict | None = Field(None, description='成本计量 jsonb（{gpu_sec, provider_usage, credits}，按 job 计量并入计费）')
    work_session_id: str | None = Field(None, description='触发的工作会话 id（续接锚点 AC-P2）')
    error: str | None = Field(None, description='失败真实错误（透传引擎，零 fake）')
    started_at: datetime | None = Field(None, description='开始时间')
    finished_at: datetime | None = Field(None, description='结束时间')


class CreateStudioRenderJobParam(StudioRenderJobSchemaBase):
    """创建视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）参数"""


class UpdateStudioRenderJobParam(StudioRenderJobSchemaBase):
    """更新视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）参数"""


class DeleteStudioRenderJobParam(SchemaBase):
    """删除视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）参数"""

    pks: list[int] = Field(description='视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库） ID 列表')


class GetStudioRenderJobDetail(StudioRenderJobSchemaBase):
    """视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
