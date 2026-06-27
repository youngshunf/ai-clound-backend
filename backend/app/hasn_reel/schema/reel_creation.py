from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ReelCreationSchemaBase(SchemaBase):
    """一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）基础模型"""
    project_id: int = Field(description='所属项目 id（FK→reel_project）')
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: str | None = Field(None, description='编排分身 hasn_id（分身路径；AgentIdentity 展示）')
    title: str | None = Field(None, description='创作标题（可从 idea 派生）')
    idea: str | None = Field(None, description='主人需求原话（派发输入快照）')
    kind: str = Field(description='发起方式 (user_pipeline:一键流水线:blue/agent_pipeline:分身代发起流水线:geekblue/agent_tools:分身工具编排:purple)')
    session_id: str | None = Field(None, description='工作会话 id（分身路径——分身工作流步骤/产物在工作会话事件流，续接锚点 AC-P2）')
    engine_task_id: str | None = Field(None, description='本地 MPT 引擎任务 id（流水线路径——进度来源；reel 引擎本地 sidecar）')
    status: str = Field(description='状态 (pending:待开始:default/running:进行中:processing/waiting_user:等你回答:gold/succeeded:已完成:green/failed:失败:red)')
    stage: str | None = Field(None, description='当前阶段文本（脚本/配音/字幕/素材/合成；透传 MPT 或会话推进）')
    progress: int = Field(description='进度 0-100')
    video_ref: dict | None = Field(None, description='成片引用 jsonb（本地优先 {kind:local,path,node_id,uploaded} 或上云后 {kind:asset,uri:hasn://asset/...}）')
    thumbnail_asset_uri: str | None = Field(None, description='首帧/缩略图 hasn://asset/')
    duration_sec: Decimal | None = Field(None, description='成片时长（秒）')
    resolution: str | None = Field(None, description='成片分辨率（如 1080x1920）')
    result_refs: dict = Field(description='中间产物引用 jsonb（文案/音频/字幕/素材；细节明细复用工作会话 session_artifacts）')
    error: str | None = Field(None, description='失败真实错误（透传引擎，零 fake）')
    started_at: datetime | None = Field(None, description='开始时间')
    finished_at: datetime | None = Field(None, description='结束时间')


class CreateReelCreationParam(ReelCreationSchemaBase):
    """创建一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数"""


class UpdateReelCreationParam(ReelCreationSchemaBase):
    """更新一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数"""


class DeleteReelCreationParam(SchemaBase):
    """删除一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）参数"""

    pks: list[int] = Field(description='一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库） ID 列表')


class GetReelCreationDetail(ReelCreationSchemaBase):
    """一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
