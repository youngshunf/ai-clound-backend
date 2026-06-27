-- 短视频（reel）项目化创作 hasn_reel：2 张表（设计 doc29）。
-- 落 schema hasn_reel（ADR-15 应用独立 schema）；PostgreSQL 语法。全新建（reel 从瘦应用恢复轻量项目管理，修正 doc19 N18）。
--
-- 设计要旨（doc29）：本质是「分身的工作流」——一次创作 = 一条 reel_creation，
--   归属项目、有进度（透传 MPT 流水线或工作会话推进）、有产物（成片 + 中间产物）、可回看。
-- 与 studio（doc22，4 表）的区别：reel 只 2 表——
--   reel_creation 合并了 studio 的 render_job（进度/状态）+ artifact（成片元数据）角色，
--   因为分身工作流的步骤明细复用现成的工作会话事件流，产物明细复用 session_artifacts，不重造。
-- reel 是 downloadable_local（引擎本地 sidecar）：engine_task_id 指本地 MPT 任务，
--   daemon 把本地进度/产物同步回云端 creation（云端权威 + daemon local_first 镜像，跨设备可见）；
--   成片二进制本地优先（doc19 N8），video_ref 存本地引用，显式才上云 hasn://asset/。
--
-- 全表公共约定（对齐 hasn_studio §3.1.1）：
--   - id bigserial PK + created_time/updated_time timestamptz；owner_hasn_id varchar NOT NULL（行级隔离，建索引）；
--   - agent_hasn_id varchar（协作分身，创建带归属资源默认取凭证身份）；枚举落 varchar + CHECK；JSON 用 jsonb；
--   - 资产存 *_asset_uri varchar = hasn://asset/...（序列化边界 resolve_assets 换 CDN 签名 URL，不存直链）；
--   - 协作列 bound_agent_id / session_id 已预留（对齐 AC-P0/AC-P2/CRX-3/DECKBIND）。

-- 应用独立 schema（ADR-15）。须先建 schema，否则 SET search_path 静默回落 public（codegen 误建 public）。
CREATE SCHEMA IF NOT EXISTS hasn_reel;
SET search_path TO hasn_reel, public;

-- ========== (1) reel_project — 短视频项目（一组创作的容器 + 默认参数） ==========
CREATE TABLE IF NOT EXISTS reel_project (
    id bigserial PRIMARY KEY,
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    title varchar(200) NOT NULL,
    description text,
    settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    cover_asset_uri varchar(512),
    bound_agent_id varchar(64),
    status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE reel_project IS '短视频项目（reel：一组创作的容器 + 默认创作参数）';
COMMENT ON COLUMN reel_project.owner_hasn_id IS '归属主人 hasn_id（行级隔离键）';
COMMENT ON COLUMN reel_project.agent_hasn_id IS '创建/默认协作分身 hasn_id（创建带归属资源默认取凭证身份）';
COMMENT ON COLUMN reel_project.title IS '项目标题（如「秋季热饮系列」）';
COMMENT ON COLUMN reel_project.description IS '项目说明';
COMMENT ON COLUMN reel_project.settings IS '默认创作参数 jsonb（比例/单段时长/音色/素材源/字幕/调性，喂创作缺省）';
COMMENT ON COLUMN reel_project.cover_asset_uri IS '封面资产 hasn://asset/（取首条成片首帧；序列化边界换 CDN 签名 URL，不存直链）';
COMMENT ON COLUMN reel_project.bound_agent_id IS '项目绑定协作分身 hasn_id（BoundAgentControl，对齐 CRX-3/DECKBIND）';
COMMENT ON COLUMN reel_project.status IS '状态 (active:进行中:green/archived:已归档:gray)';
CREATE INDEX IF NOT EXISTS idx_reel_project_owner_status ON reel_project (owner_hasn_id, status);
CREATE INDEX IF NOT EXISTS idx_reel_project_agent ON reel_project (agent_hasn_id);

-- ========== (2) reel_creation — 一次创作（分身工作流 + 进度 + 产物 + 历史） ==========
CREATE TABLE IF NOT EXISTS reel_creation (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL,
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    title varchar(200),
    idea text,
    kind varchar(16) NOT NULL CHECK (kind IN ('user_pipeline', 'agent_pipeline', 'agent_tools')),
    session_id varchar(64),
    engine_task_id varchar(64),
    status varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'waiting_user', 'succeeded', 'failed')),
    stage varchar(120),
    progress int NOT NULL DEFAULT 0,
    video_ref jsonb,
    thumbnail_asset_uri varchar(512),
    duration_sec numeric(10, 2),
    resolution varchar(20),
    result_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    started_at timestamptz,
    finished_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE reel_creation IS '一次短视频创作（分身工作流 + 进度 + 产物 + 历史；引擎/会话权威态经 daemon 同步落库）';
COMMENT ON COLUMN reel_creation.project_id IS '所属项目 id（FK→reel_project）';
COMMENT ON COLUMN reel_creation.owner_hasn_id IS '归属主人 hasn_id（行级隔离键）';
COMMENT ON COLUMN reel_creation.agent_hasn_id IS '编排分身 hasn_id（分身路径；AgentIdentity 展示）';
COMMENT ON COLUMN reel_creation.title IS '创作标题（可从 idea 派生）';
COMMENT ON COLUMN reel_creation.idea IS '主人需求原话（派发输入快照）';
COMMENT ON COLUMN reel_creation.kind IS '发起方式 (user_pipeline:一键流水线:blue/agent_pipeline:分身代发起流水线:geekblue/agent_tools:分身工具编排:purple)';
COMMENT ON COLUMN reel_creation.session_id IS '工作会话 id（分身路径——分身工作流步骤/产物在工作会话事件流，续接锚点 AC-P2）';
COMMENT ON COLUMN reel_creation.engine_task_id IS '本地 MPT 引擎任务 id（流水线路径——进度来源；reel 引擎本地 sidecar）';
COMMENT ON COLUMN reel_creation.status IS '状态 (pending:待开始:default/running:进行中:processing/waiting_user:等你回答:gold/succeeded:已完成:green/failed:失败:red)';
COMMENT ON COLUMN reel_creation.stage IS '当前阶段文本（脚本/配音/字幕/素材/合成；透传 MPT 或会话推进）';
COMMENT ON COLUMN reel_creation.progress IS '进度 0-100';
COMMENT ON COLUMN reel_creation.video_ref IS '成片引用 jsonb（本地优先 {kind:local,path,node_id,uploaded} 或上云后 {kind:asset,uri:hasn://asset/...}）';
COMMENT ON COLUMN reel_creation.thumbnail_asset_uri IS '首帧/缩略图 hasn://asset/';
COMMENT ON COLUMN reel_creation.duration_sec IS '成片时长（秒）';
COMMENT ON COLUMN reel_creation.resolution IS '成片分辨率（如 1080x1920）';
COMMENT ON COLUMN reel_creation.result_refs IS '中间产物引用 jsonb（文案/音频/字幕/素材；细节明细复用工作会话 session_artifacts）';
COMMENT ON COLUMN reel_creation.error IS '失败真实错误（透传引擎，零 fake）';
COMMENT ON COLUMN reel_creation.started_at IS '开始时间';
COMMENT ON COLUMN reel_creation.finished_at IS '结束时间';
CREATE INDEX IF NOT EXISTS idx_reel_creation_project ON reel_creation (project_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_reel_creation_owner ON reel_creation (owner_hasn_id);
CREATE INDEX IF NOT EXISTS idx_reel_creation_status ON reel_creation (status);
CREATE INDEX IF NOT EXISTS idx_reel_creation_session ON reel_creation (session_id);
