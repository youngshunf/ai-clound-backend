-- OpenPencil 矢量设计工具接入应用 hasn_design：云端轻登记表（设计 doc27 §5.9-2）。
-- 落 schema hasn_design（ADR-15 应用独立 schema）；PostgreSQL 语法。全新建（无存量、无停机约束）。
--
-- 本地优先 · 云端轻登记（设计 §5.9）：`.op` 源文件**本地优先、默认不上云**（重、活编辑态、owner 私有，
--   对齐 reel/copilot），活态权威在 daemon 本地 SQLite `design_projects`（§5.9-1，含 op_path/thumbnail_path）；
--   云端 `hasn_design_project` 只存**轻登记元数据**做跨设备发现 + 协作 + 企业归属，跨设备经 local_first 合并。
--   **故本表不含 op_path/缩略图本地路径**——`.op` 内容不入此表，B 档源文件交接才经 hasn.asset.create 显式上传为 asset。
--
-- 全表约定（对齐 studio_project §3.1.1）：
--   - id bigserial PK + created_time/updated_time timestamptz（来自 fba Base DateTimeMixin，本 SQL 仅建表用）；
--     ⚠️ 设计 §5.9-2 写「id UUID PK」，但 fba codegen `model.jinja` 恒生成 bigint `id_key`（无法生成 UUID PK，
--     studio_project 同样落 bigint）——遵「严禁手写 model，必须 codegen」铁律，PK 取 bigint 与 codegen 产物对齐。
--   - owner_hasn_id varchar NOT NULL（行级隔离键，建索引）；service 层强制按身份过滤
--     （= 设计 §5.9-2 的「owner_id 数据隔离」在本库的具体落地键，对齐 studio/plan 全用 owner_hasn_id）；
--   - 枚举落 varchar + CHECK（迁移友好，不用 PG enum）；字典字段 COMMENT ON (value:label:color) 格式；JSON 用 jsonb；
--   - 资产一律存 *_asset_uri varchar = hasn://asset/...（序列化边界 resolve_assets 换 CDN 签名 URL，不存直链）；
--   - latest_artifact_id 存 public.hasn_artifacts.artifact_id（art_<ulid> 客户端无关公开标识；设计 §5.9-2 写 FK
--     hasn_artifacts，但 hasn_artifacts PK 是 bigint + artifact_id 字符串，故存字符串公开 id 而非硬 FK，
--     对齐 studio 不建跨表硬 FK 只存 id 的做法）。
-- 复用平台底座、不自建：A 成品分享→resource_share / M18 hasn_publish；通用产物索引→public.hasn_artifacts（AF-*）；
--   派发→复用 work_session（零新表）；企业归属→enterprise_id（GE 双模）。

-- 应用独立 schema（ADR-15）。须先建 schema，否则 SET search_path 静默回落 public（codegen 误建 public）。
CREATE SCHEMA IF NOT EXISTS hasn_design;
SET search_path TO hasn_design, public;

-- ========== hasn_design_project — 设计项目（= 一个 OpenPencil 文档 = 一个 .op，云端轻登记，§5.9-2） ==========
CREATE TABLE IF NOT EXISTS hasn_design_project (
    id bigserial PRIMARY KEY,
    owner_hasn_id varchar(64) NOT NULL,
    name varchar(200) NOT NULL,
    description text,
    thumbnail_asset_uri varchar(512),
    bound_agent_id varchar(64),
    canvas_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    latest_artifact_id varchar(64),
    enterprise_id varchar(64),
    status varchar(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    visibility varchar(16) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'shared', 'public')),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE hasn_design_project IS '设计项目（矢量设计 design：云端轻登记元数据，源文件本地优先）';
COMMENT ON COLUMN hasn_design_project.owner_hasn_id IS '归属主人 hasn_id（行级隔离键；= 设计 §5.9-2 的 owner_id 数据隔离）';
COMMENT ON COLUMN hasn_design_project.name IS '项目名（= OpenPencil 文档名）';
COMMENT ON COLUMN hasn_design_project.description IS '项目说明';
COMMENT ON COLUMN hasn_design_project.thumbnail_asset_uri IS '缩略图资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）';
COMMENT ON COLUMN hasn_design_project.bound_agent_id IS '绑定设计分身 hasn_id（BoundAgentControl，对齐 deck/studio bound_agent_id）';
COMMENT ON COLUMN hasn_design_project.canvas_meta IS '画布轻元数据 jsonb（{width,height,page_count}）';
COMMENT ON COLUMN hasn_design_project.latest_artifact_id IS '最近导出产物公开标识（public.hasn_artifacts.artifact_id，art_<ulid>；非硬 FK）';
COMMENT ON COLUMN hasn_design_project.enterprise_id IS '企业归属 id（GE 双模：个人项目为空，企业项目归企业）';
COMMENT ON COLUMN hasn_design_project.status IS '状态 (draft:草稿:blue/active:活跃:green/archived:归档:gray)';
COMMENT ON COLUMN hasn_design_project.visibility IS '可见性 (private:私有:gray/shared:已分享:blue/public:公开:green)';
CREATE INDEX IF NOT EXISTS idx_hasn_design_project_owner_status ON hasn_design_project (owner_hasn_id, status, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_hasn_design_project_bound_agent ON hasn_design_project (bound_agent_id);
CREATE INDEX IF NOT EXISTS idx_hasn_design_project_enterprise ON hasn_design_project (enterprise_id);
