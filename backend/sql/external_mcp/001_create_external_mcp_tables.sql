-- 第三方 MCP 网关接入 external_mcp：4 张表（P7 实施清单 §4 / 事实源 10、02）。
-- 落 schema external_mcp（ADR-15 应用独立 schema）；PostgreSQL 语法；snake_case；
-- created_time/updated_time timestamptz。
--
-- 设计要旨（10/02）：HASN 同时是「MCP 网关 / 聚合器」——owner 或平台配置一次第三方 MCP server，
--   网关自省其工具、归一注册进统一目录、所有 Agent 经 hasn.ext.{ns}.* 渐进发现并代理调用。
--   首个用例：企查查（qcc）远程服务型（hosting=remote_service，origin=system，平台 key）。
-- 承载形态第一性区分（02 §4.1）：
--   remote_service 远程服务型（远程 HTTP/SSE/WS，云端直连，execution_location=cloud，无本机安装）；
--   local_process 本地进程型（stdio/本机回环，每设备自装，execution_location=local）。
-- 凭据一律 secret://（02 §6）：明文不落库、不回显、不下发；写入加密落 external_mcp_secret。
-- 平台 key（system-origin）用量治理（10 §7.2）：按 (owner,agent,mcp_id,tool) 记账 + per-owner 配额 + 限流。
-- 职责边界（10 §10）：网关只管「协议代理 + 工具目录缓存」，业务结果入库由上层业务模块二次加工。

CREATE SCHEMA IF NOT EXISTS external_mcp;
SET search_path TO external_mcp, public;

-- ========== (1) external_mcp_server — 第三方 MCP server 配置（owner 或平台·system） ==========
CREATE TABLE IF NOT EXISTS external_mcp_server (
    id bigserial PRIMARY KEY,
    mcp_id varchar(40) NOT NULL,
    name varchar(64) NOT NULL,
    display_name varchar(120),
    hosting varchar(20) NOT NULL CHECK (hosting IN ('remote_service', 'local_process')),
    transport varchar(20) NOT NULL CHECK (transport IN ('http', 'websocket', 'sse', 'stdio')),
    endpoint varchar(1024),
    command varchar(256),
    args jsonb NOT NULL DEFAULT '[]'::jsonb,
    env jsonb NOT NULL DEFAULT '{}'::jsonb,
    headers jsonb NOT NULL DEFAULT '{}'::jsonb,
    origin varchar(20) NOT NULL DEFAULT 'owner' CHECK (origin IN ('system', 'owner', 'marketplace')),
    owner_hasn_id varchar(64),
    scope varchar(20) NOT NULL DEFAULT 'owner',
    risk_level varchar(16) NOT NULL DEFAULT 'medium' CHECK (risk_level IN ('low', 'medium', 'high')),
    advertised_tools_cache jsonb NOT NULL DEFAULT '[]'::jsonb,
    tools_hash varchar(80),
    advertised_tools_cached_at timestamptz,
    health_status varchar(20) NOT NULL DEFAULT 'unknown' CHECK (health_status IN ('unknown', 'unstarted', 'healthy', 'unhealthy', 'circuit_broken', 'not_installed')),
    health_checked_at timestamptz,
    health_detail text,
    per_owner_daily_quota int NOT NULL DEFAULT 0,
    rate_limit_per_min int NOT NULL DEFAULT 0,
    status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE external_mcp_server IS '第三方 MCP server 配置（网关自省与代理的承载单元；owner 自配或平台 system 预置）';
COMMENT ON COLUMN external_mcp_server.mcp_id IS 'server 业务主键 mcp_{ulid}（注册时生成，全局唯一）';
COMMENT ON COLUMN external_mcp_server.name IS 'server_namespace（如 qcc/gmail，全局唯一，映射 hasn.ext.{name}.*）';
COMMENT ON COLUMN external_mcp_server.display_name IS '展示名（中文友好名，UI 用）';
COMMENT ON COLUMN external_mcp_server.hosting IS '承载形态 (remote_service:远程服务型:blue/local_process:本地进程型:green)';
COMMENT ON COLUMN external_mcp_server.transport IS '传输 (http:HTTP/websocket:WebSocket/sse:SSE/stdio:本机stdio)';
COMMENT ON COLUMN external_mcp_server.endpoint IS '远程端点 URL（remote_service 必填；http/ws/sse）';
COMMENT ON COLUMN external_mcp_server.command IS '本机启动命令（local_process stdio，如 npx）';
COMMENT ON COLUMN external_mcp_server.args IS '本机启动参数 jsonb（local_process）';
COMMENT ON COLUMN external_mcp_server.env IS '环境变量 jsonb（值可为 secret:// 引用；明文拒绝）';
COMMENT ON COLUMN external_mcp_server.headers IS '请求头 jsonb（remote_service；值可为 secret:// 引用，如 Authorization）';
COMMENT ON COLUMN external_mcp_server.origin IS '配置归属 (system:平台预置:gold/owner:用户自配:blue/marketplace:市场安装:purple)';
COMMENT ON COLUMN external_mcp_server.owner_hasn_id IS '归属主人 hasn_id（owner/marketplace-origin 必填；system-origin 为空=全平台共享）';
COMMENT ON COLUMN external_mcp_server.scope IS '可见范围（owner=该主人及其 Agent）';
COMMENT ON COLUMN external_mcp_server.risk_level IS '风险等级 (low:低:green/medium:中:orange/high:高:red)';
COMMENT ON COLUMN external_mcp_server.advertised_tools_cache IS '自省到的第三方工具缓存 jsonb（tools/list 归一 ToolMeta[]，目录层缓存非调用缓存）';
COMMENT ON COLUMN external_mcp_server.tools_hash IS '工具集 hash（tools/list 内容指纹，变更触发 re-probe / SCHEMA_HASH_MISMATCH）';
COMMENT ON COLUMN external_mcp_server.advertised_tools_cached_at IS '工具缓存刷新时间';
COMMENT ON COLUMN external_mcp_server.health_status IS '健康 (unknown:未知/unstarted:未拉起/healthy:健康:green/unhealthy:不健康:orange/circuit_broken:熔断:red/not_installed:未安装)';
COMMENT ON COLUMN external_mcp_server.health_checked_at IS '最近 probe 时间';
COMMENT ON COLUMN external_mcp_server.health_detail IS '健康详情（失败真实原因，零 fake）';
COMMENT ON COLUMN external_mcp_server.per_owner_daily_quota IS 'system-origin 平台 key 的 per-owner 每日调用配额（0=不限；10 §7.2）';
COMMENT ON COLUMN external_mcp_server.rate_limit_per_min IS 'system-origin 平台 key 的 per-owner 每分钟限流（0=不限；10 §7.2）';
COMMENT ON COLUMN external_mcp_server.status IS '状态 (active:启用:green/disabled:停用:red)';
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_mcp_server_mcp_id ON external_mcp_server (mcp_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_mcp_server_name ON external_mcp_server (name);
CREATE INDEX IF NOT EXISTS idx_external_mcp_server_owner ON external_mcp_server (owner_hasn_id);
CREATE INDEX IF NOT EXISTS idx_external_mcp_server_origin ON external_mcp_server (origin, status);

-- ========== (2) external_mcp_binding — Agent ↔ server 绑定（owner 授权某 Agent 可用） ==========
CREATE TABLE IF NOT EXISTS external_mcp_binding (
    id bigserial PRIMARY KEY,
    binding_id varchar(40) NOT NULL,
    agent_hasn_id varchar(64) NOT NULL,
    owner_hasn_id varchar(64) NOT NULL,
    mcp_id varchar(40) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
    owner_authorized_at timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE external_mcp_binding IS 'Agent ↔ 第三方 MCP server 绑定（gate 2：owner 授权某 Agent 可用该 server 的哪些工具）';
COMMENT ON COLUMN external_mcp_binding.binding_id IS 'binding 业务主键 mcb_{ulid}';
COMMENT ON COLUMN external_mcp_binding.agent_hasn_id IS '被授权 Agent hasn_id';
COMMENT ON COLUMN external_mcp_binding.owner_hasn_id IS '授权主人 hasn_id（行级隔离 + 平台 key 用量归因键）';
COMMENT ON COLUMN external_mcp_binding.mcp_id IS '绑定的 server mcp_id（→external_mcp_server.mcp_id）';
COMMENT ON COLUMN external_mcp_binding.enabled IS '是否启用（owner 可临时停用，停用即移出可调用发现集）';
COMMENT ON COLUMN external_mcp_binding.allowed_tools IS '授权工具映射 jsonb（[{raw_name, tool_name}]；只有命中的工具可发现可调）';
COMMENT ON COLUMN external_mcp_binding.owner_authorized_at IS '主人授权时间';
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_mcp_binding_agent_mcp ON external_mcp_binding (agent_hasn_id, mcp_id);
CREATE INDEX IF NOT EXISTS idx_external_mcp_binding_owner ON external_mcp_binding (owner_hasn_id);
CREATE INDEX IF NOT EXISTS idx_external_mcp_binding_mcp ON external_mcp_binding (mcp_id);

-- ========== (3) external_mcp_secret — secret:// 凭据密文存储（明文永不落库） ==========
CREATE TABLE IF NOT EXISTS external_mcp_secret (
    id bigserial PRIMARY KEY,
    secret_uri varchar(256) NOT NULL,
    origin varchar(20) NOT NULL DEFAULT 'owner' CHECK (origin IN ('system', 'owner', 'marketplace')),
    owner_hasn_id varchar(64),
    ciphertext text NOT NULL,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE external_mcp_secret IS 'secret:// 凭据密文存储（Fernet 加密；明文不落库/不回显/不下发；10 §7.1 生命周期）';
COMMENT ON COLUMN external_mcp_secret.secret_uri IS 'secret:// 引用 URI（如 secret://system/qcc/bearer-token；全局唯一）';
COMMENT ON COLUMN external_mcp_secret.origin IS '凭据归属 (system:平台/owner:用户/marketplace:市场)';
COMMENT ON COLUMN external_mcp_secret.owner_hasn_id IS 'owner-origin 凭据归属主人 hasn_id（system-origin 为空）';
COMMENT ON COLUMN external_mcp_secret.ciphertext IS '密文（key_encryption Fernet 加密；明文经此加密落库，仅建连时解密注入）';
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_mcp_secret_uri ON external_mcp_secret (secret_uri);
CREATE INDEX IF NOT EXISTS idx_external_mcp_secret_owner ON external_mcp_secret (owner_hasn_id);

-- ========== (4) external_mcp_usage — 平台 key 用量归因/配额账本（10 §7.2） ==========
CREATE TABLE IF NOT EXISTS external_mcp_usage (
    id bigserial PRIMARY KEY,
    mcp_id varchar(40) NOT NULL,
    caller_owner_hasn_id varchar(64) NOT NULL,
    caller_agent_hasn_id varchar(64) NOT NULL,
    tool_name varchar(200) NOT NULL,
    origin varchar(20) NOT NULL DEFAULT 'system',
    trace_id varchar(80),
    success boolean NOT NULL DEFAULT true,
    cost_units int NOT NULL DEFAULT 1,
    day_bucket date NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')::date,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE external_mcp_usage IS '第三方 MCP 调用用量账本（平台 key 按 (owner,agent,mcp_id,tool) 记账 + per-owner 每日配额聚合 + 计费归属）';
COMMENT ON COLUMN external_mcp_usage.mcp_id IS '被调 server mcp_id';
COMMENT ON COLUMN external_mcp_usage.caller_owner_hasn_id IS '调用方主人 hasn_id（计费/配额归属）';
COMMENT ON COLUMN external_mcp_usage.caller_agent_hasn_id IS '调用方 Agent hasn_id';
COMMENT ON COLUMN external_mcp_usage.tool_name IS '调用的 canonical 工具名（hasn.ext.{ns}.{tool}）';
COMMENT ON COLUMN external_mcp_usage.origin IS '凭据归属（system=平台付费摊调用方/owner=用户自带）';
COMMENT ON COLUMN external_mcp_usage.trace_id IS 'trace_id（贯穿发现/调用/审计）';
COMMENT ON COLUMN external_mcp_usage.success IS '调用是否成功';
COMMENT ON COLUMN external_mcp_usage.cost_units IS '计费单元（默认 1 次/单位）';
COMMENT ON COLUMN external_mcp_usage.day_bucket IS 'UTC 自然日（per-owner 每日配额聚合键）';
CREATE INDEX IF NOT EXISTS idx_external_mcp_usage_quota ON external_mcp_usage (mcp_id, caller_owner_hasn_id, day_bucket);
CREATE INDEX IF NOT EXISTS idx_external_mcp_usage_owner ON external_mcp_usage (caller_owner_hasn_id, created_time DESC);
