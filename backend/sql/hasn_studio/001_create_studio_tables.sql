-- OpenMontage 统一视频引擎接入应用 hasn_studio：4 张表（设计 doc22 §3.1）。
-- 落 schema hasn_studio（ADR-15 应用独立 schema）；PostgreSQL 语法。
-- 全新建（无存量、无停机约束）。cloud-brokered：产品级数据权威在唤星 PG（不变量 #4），
--   引擎服务（hasn-apps/montage-engine-service）无产品表，只持渲染运行态（crash-only 可重跑）。
--
-- 全表公共约定（设计 §3.1.1）：
--   - id bigserial PK + created_time/updated_time timestamptz（来自 fba Base DateTimeMixin，本 SQL 仅建表用）；
--   - owner_hasn_id varchar NOT NULL（行级隔离，建索引）；service 层强制按身份过滤；
--   - agent_hasn_id varchar（协作分身，创建带归属资源默认取凭证身份，对齐 PLANFIX-6）；
--   - 枚举落 varchar + CHECK（迁移友好，不用 PG enum）；字典字段 COMMENT ON (value:label:color) 格式；JSON 用 jsonb；
--   - 资产一律存 *_asset_uri varchar = hasn://asset/...（序列化边界 resolve_assets 换 CDN 签名 URL，不存直链）；
--   - 协作列 bound_agent_id / origin_type / active_work_session_id 已预留（对齐 AC-P0/AC-P2/CRX-3/DECKBIND）。
-- 删除语义（§3.1.3）：project archived 软删（保留 render_job/artifact 审计）；artifact 删=解引用 + 标记（字节按 asset GC）；
--   失败 render_job 保留 error 留痕。
-- 复用平台底座、不自建（§3.1.3）：协作共享→resource_share（仅加 resource_type 取值，零新表）；对外发布→M18 hasn_publish；
--   通用产物索引→public.hasn_artifacts（AF-*）；媒体 provider 凭据→hasn_app_credential + key_encryption；
--   管线目录→引擎 GET /v1/pipelines 元数据（非 PG 表）。

-- 应用独立 schema（ADR-15）。须先建 schema，否则 SET search_path 静默回落 public（codegen 误建 public）。
CREATE SCHEMA IF NOT EXISTS hasn_studio;
SET search_path TO hasn_studio, public;

-- ========== 3.1.2 (1) studio_project — 视频项目（管线/素材/成品的容器） ==========
CREATE TABLE IF NOT EXISTS studio_project (
    id bigserial PRIMARY KEY,
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    title varchar(200) NOT NULL,
    description text,
    default_pipeline_key varchar(80),
    settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    cover_asset_uri varchar(512),
    bound_agent_id varchar(64),
    status varchar(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE studio_project IS '视频项目（统一视频引擎 studio：管线/素材/成品的容器）';
COMMENT ON COLUMN studio_project.owner_hasn_id IS '归属主人 hasn_id（行级隔离键）';
COMMENT ON COLUMN studio_project.agent_hasn_id IS '创建/默认协作分身 hasn_id（创建带归属资源默认取凭证身份，PLANFIX-6）';
COMMENT ON COLUMN studio_project.title IS '项目标题';
COMMENT ON COLUMN studio_project.description IS '项目说明';
COMMENT ON COLUMN studio_project.default_pipeline_key IS '项目默认管线 key（来自引擎 pipeline_defs，非 PG 表）';
COMMENT ON COLUMN studio_project.settings IS '项目设置 jsonb（调性/分辨率/默认参数/品牌，喂 run_pipeline 缺省）';
COMMENT ON COLUMN studio_project.cover_asset_uri IS '封面资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）';
COMMENT ON COLUMN studio_project.bound_agent_id IS '项目绑定协作分身 hasn_id（BoundAgentControl，对齐 CRX-3/DECKBIND）';
COMMENT ON COLUMN studio_project.status IS '状态 (draft:草稿:blue/active:进行中:green/archived:已归档:gray)';
CREATE INDEX IF NOT EXISTS idx_studio_project_owner_status ON studio_project (owner_hasn_id, status);
CREATE INDEX IF NOT EXISTS idx_studio_project_agent ON studio_project (agent_hasn_id);

-- ========== 3.1.2 (2) studio_asset — 素材库（输入素材） ==========
CREATE TABLE IF NOT EXISTS studio_asset (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL,
    owner_hasn_id varchar(64) NOT NULL,
    kind varchar(16) NOT NULL CHECK (kind IN ('script', 'image', 'audio', 'video', 'subtitle', 'voiceover', 'bgm', 'font')),
    asset_uri varchar(512) NOT NULL,
    source varchar(16) NOT NULL DEFAULT 'upload' CHECK (source IN ('upload', 'generated', 'stock', 'provider')),
    title varchar(200),
    meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE studio_asset IS '视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）';
COMMENT ON COLUMN studio_asset.project_id IS '所属项目 id（FK→studio_project）';
COMMENT ON COLUMN studio_asset.owner_hasn_id IS '归属主人 hasn_id（冗余免 join，行级隔离）';
COMMENT ON COLUMN studio_asset.kind IS '素材类型 (script:脚本:blue/image:图片:cyan/audio:音频:purple/video:视频:geekblue/subtitle:字幕:gold/voiceover:配音:magenta/bgm:配乐:lime/font:字体:default)';
COMMENT ON COLUMN studio_asset.asset_uri IS '素材本体 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）';
COMMENT ON COLUMN studio_asset.source IS '素材来源 (upload:主人上传:blue/generated:分身生成:green/stock:库存:cyan/provider:外部provider:orange)';
COMMENT ON COLUMN studio_asset.title IS '素材显示名';
COMMENT ON COLUMN studio_asset.meta IS '素材元数据 jsonb（时长/分辨率/语言/采样率）';
CREATE INDEX IF NOT EXISTS idx_studio_asset_project_kind ON studio_asset (project_id, kind);
CREATE INDEX IF NOT EXISTS idx_studio_asset_owner ON studio_asset (owner_hasn_id);

-- ========== 3.1.2 (3) studio_render_job — 渲染任务（运行态镜像 + 进度/成本 + 审计） ==========
CREATE TABLE IF NOT EXISTS studio_render_job (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL,
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    pipeline_key varchar(80) NOT NULL,
    input jsonb NOT NULL DEFAULT '{}'::jsonb,
    engine_job_id varchar(64),
    status varchar(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    progress int NOT NULL DEFAULT 0,
    stage varchar(120),
    cost jsonb,
    work_session_id varchar(64),
    error text,
    started_at timestamptz,
    finished_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE studio_render_job IS '视频渲染任务（运行态镜像 + 进度/成本 + 审计；引擎权威态经云端轮询/webhook 同步落库）';
COMMENT ON COLUMN studio_render_job.project_id IS '所属项目 id（FK→studio_project）';
COMMENT ON COLUMN studio_render_job.owner_hasn_id IS '归属主人 hasn_id（行级隔离键）';
COMMENT ON COLUMN studio_render_job.agent_hasn_id IS '发起分身 hasn_id';
COMMENT ON COLUMN studio_render_job.pipeline_key IS '跑哪条管线 key（run_pipeline，来自引擎 pipeline_defs）';
COMMENT ON COLUMN studio_render_job.input IS '入参快照 jsonb（脚本/素材引用/参数，不回指项目当前值）';
COMMENT ON COLUMN studio_render_job.engine_job_id IS '引擎侧 job 标识（云端轮询/回流用）';
COMMENT ON COLUMN studio_render_job.status IS '状态 (queued:排队中:blue/running:执行中:processing/succeeded:成功:green/failed:失败:red/canceled:已取消:default)';
COMMENT ON COLUMN studio_render_job.progress IS '进度 0-100';
COMMENT ON COLUMN studio_render_job.stage IS '当前阶段文本（生成第3镜/配音/合成，喂 CRX-1 进度抽屉）';
COMMENT ON COLUMN studio_render_job.cost IS '成本计量 jsonb（{gpu_sec, provider_usage, credits}，按 job 计量并入计费）';
COMMENT ON COLUMN studio_render_job.work_session_id IS '触发的工作会话 id（续接锚点 AC-P2）';
COMMENT ON COLUMN studio_render_job.error IS '失败真实错误（透传引擎，零 fake）';
COMMENT ON COLUMN studio_render_job.started_at IS '开始时间';
COMMENT ON COLUMN studio_render_job.finished_at IS '结束时间';
CREATE INDEX IF NOT EXISTS idx_studio_render_job_project ON studio_render_job (project_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_studio_render_job_owner ON studio_render_job (owner_hasn_id);
CREATE INDEX IF NOT EXISTS idx_studio_render_job_status ON studio_render_job (status);

-- ========== 3.1.2 (4) studio_artifact — 成品（最终视频 + 元数据） ==========
CREATE TABLE IF NOT EXISTS studio_artifact (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL,
    render_job_id bigint,
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    title varchar(200) NOT NULL,
    pipeline_key varchar(80),
    video_asset_uri varchar(512) NOT NULL,
    thumbnail_asset_uri varchar(512),
    duration_sec numeric(10, 2),
    resolution varchar(20),
    status varchar(16) NOT NULL DEFAULT 'generating' CHECK (status IN ('generating', 'reviewing', 'completed', 'failed')),
    origin_type varchar(16) NOT NULL DEFAULT 'app',
    active_work_session_id varchar(64),
    meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE studio_artifact IS '视频成品（最终视频 + 元数据；与通用索引 public.hasn_artifacts 同引 hasn://asset/）';
COMMENT ON COLUMN studio_artifact.project_id IS '所属项目 id（FK→studio_project）';
COMMENT ON COLUMN studio_artifact.render_job_id IS '产出该成品的渲染 job id（FK→studio_render_job；可空兼容外部导入）';
COMMENT ON COLUMN studio_artifact.owner_hasn_id IS '归属主人 hasn_id（行级隔离键）';
COMMENT ON COLUMN studio_artifact.agent_hasn_id IS '协作分身 hasn_id（AgentIdentity 展示）';
COMMENT ON COLUMN studio_artifact.title IS '成品标题';
COMMENT ON COLUMN studio_artifact.pipeline_key IS '用了哪条管线 key（成品卡展示）';
COMMENT ON COLUMN studio_artifact.video_asset_uri IS '成片 mp4 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）';
COMMENT ON COLUMN studio_artifact.thumbnail_asset_uri IS '缩略图/首帧 hasn://asset/';
COMMENT ON COLUMN studio_artifact.duration_sec IS '时长（秒）';
COMMENT ON COLUMN studio_artifact.resolution IS '分辨率（如 1080x1920）';
COMMENT ON COLUMN studio_artifact.status IS '状态 (generating:生成中:blue/reviewing:待审核:gold/completed:已完成:green/failed:失败:red)';
COMMENT ON COLUMN studio_artifact.origin_type IS '续接归属来源 (app:应用:blue)（AC-P2）';
COMMENT ON COLUMN studio_artifact.active_work_session_id IS '「改一版」回到同一工作会话 id（AC-P2）';
COMMENT ON COLUMN studio_artifact.meta IS '成品元数据 jsonb（provider 用量/成本/分镜数）';
CREATE INDEX IF NOT EXISTS idx_studio_artifact_owner_status ON studio_artifact (owner_hasn_id, status, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_studio_artifact_project ON studio_artifact (project_id);
CREATE INDEX IF NOT EXISTS idx_studio_artifact_render_job ON studio_artifact (render_job_id);
