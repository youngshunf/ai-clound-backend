-- 工作流应用产品化 P3-cloud 模板层：workflow_template 表 + workflow 溯源列 + domain 字典 seed
-- （模块 12 · 场景即模板 doc11 §4；施工清单 doc94 §10-P3）
--
-- 「场景就是工作流」：模板（workflow_template）声明领域链路蓝图（graph_spec 节点+边），
-- 实例化直接生成 workflow + workflow_run（实例化的真实物化归 P3-daemon：读镜像模板→本地建
-- workflow+node+run→sync 上云；云端只提供本表的读 API 供 daemon 拉取模板蓝图）。本迁移只建
-- 「模板本体表 + 实例溯源列 + 领域分组字典」，不建 run。
--
-- domain 非空 = 场景模板（呈现走场景皮肤）；NULL = 普通工作流模板。分组显示元数据（组名/图标/色）
-- 走系统字典 workflow_template_domain（value:label:color 格式），不建表；组内计数派生。
--
-- 端云稳定标识：template_uuid（前缀 wft_）与本仓所有跨端实体一致（nd_/ndr_ 同族），用稳定
-- *_uuid 同步（非 bigint id）；template_key 为图内稳定业务键，全局唯一。
--
-- 幂等：可重复执行（IF NOT EXISTS + DO 块 IF NOT EXISTS 守卫 + ON CONFLICT）；PostgreSQL 语法。
-- 执行：psql -d huanxing -f backend/sql/hasn_task/migrations/2026-07-14-workflow-template.sql

CREATE SCHEMA IF NOT EXISTS hasn_task;

-- ============================================================
-- 1. hasn_task.workflow_template（模板本体表 · 云端权威）
--    字段严格照 doc11 §4.2（含审计补的 4 字段 tagline/sort_order/source+market_ref/sku_ref）。
--    上架态不落本表：市场发布物是独立 listing 行（§8.2），本体只留 source/market_ref 溯源。
-- ============================================================
CREATE TABLE IF NOT EXISTS hasn_task.workflow_template (
    "id"              bigserial PRIMARY KEY,
    "template_uuid"   varchar(64) NOT NULL UNIQUE,
    "template_key"    varchar(64) NOT NULL UNIQUE,
    "domain"          varchar(32),
    "name"            varchar(64) NOT NULL DEFAULT '',
    "tagline"         varchar(64),
    "description"     text,
    "sort_order"      integer NOT NULL DEFAULT 0,
    "icon"            varchar(32),
    "accent"          varchar(16),
    "graph_spec"      jsonb NOT NULL DEFAULT '{}'::jsonb,
    "is_builtin"      boolean NOT NULL DEFAULT false,
    "builtin_key"     varchar(64),
    "status"          varchar(16) NOT NULL DEFAULT 'draft',
    "owner_id"        varchar(40),
    "source"          varchar(16) NOT NULL DEFAULT 'owner',
    "market_ref"      varchar(255),
    "sku_ref"         varchar(64),
    "version"         integer NOT NULL DEFAULT 1,
    "created_time"    timestamptz(6) NOT NULL DEFAULT now(),
    "updated_time"    timestamptz(6),

    CONSTRAINT "chk_workflow_template_status"
        CHECK ("status" IN ('draft', 'active', 'coming_soon', 'archived')),
    CONSTRAINT "chk_workflow_template_source"
        CHECK ("source" IN ('builtin', 'owner', 'agent', 'marketplace'))
);

CREATE INDEX IF NOT EXISTS "idx_workflow_template_domain"
    ON hasn_task.workflow_template ("domain");
CREATE INDEX IF NOT EXISTS "idx_workflow_template_owner"
    ON hasn_task.workflow_template ("owner_id");
CREATE INDEX IF NOT EXISTS "idx_workflow_template_status"
    ON hasn_task.workflow_template ("status");

COMMENT ON TABLE  hasn_task.workflow_template IS '工作流模板（场景=领域模板；graph_spec 声明图蓝图，实例化物化为 workflow+workflow_node）';
COMMENT ON COLUMN hasn_task.workflow_template."template_uuid" IS '端云稳定模板 UUID（前缀 wft_，同步主键）';
COMMENT ON COLUMN hasn_task.workflow_template."template_key" IS '模板键（one_person_company/fin_research…），全局唯一';
COMMENT ON COLUMN hasn_task.workflow_template."domain" IS '领域分组 code（startup/finance/office/professional…）；非空=场景模板走场景皮肤，NULL=普通工作流模板。显示元数据走字典 workflow_template_domain';
COMMENT ON COLUMN hasn_task.workflow_template."name" IS '展示名';
COMMENT ON COLUMN hasn_task.workflow_template."tagline" IS '一句话标签（画廊卡短语，如「一个人跑通一家公司」；与 description 并存）';
COMMENT ON COLUMN hasn_task.workflow_template."description" IS '链路详述';
COMMENT ON COLUMN hasn_task.workflow_template."sort_order" IS '展示排序（首页模板条取前 N；排序第一的 active 场景模板即 hero 推荐位）';
COMMENT ON COLUMN hasn_task.workflow_template."icon" IS '图标 key（lucide kebab 名）';
COMMENT ON COLUMN hasn_task.workflow_template."accent" IS '主题强调色（brand/teal/indigo/rose…）';
COMMENT ON COLUMN hasn_task.workflow_template."graph_spec" IS '图蓝图 {nodes:[],edges:[]}（节点声明见 doc11 §4.3；实例化时物化为 workflow_node 行）';
COMMENT ON COLUMN hasn_task.workflow_template."is_builtin" IS '官方内置标记（对齐 hub 官方内置不变量）';
COMMENT ON COLUMN hasn_task.workflow_template."builtin_key" IS '内置溯源键';
COMMENT ON COLUMN hasn_task.workflow_template."status" IS '状态 (draft:草稿:gray/active:启用:green/coming_soon:即将上线:orange/archived:已归档:gray)';
COMMENT ON COLUMN hasn_task.workflow_template."owner_id" IS '自定义模板归属主人（内置 NULL）';
COMMENT ON COLUMN hasn_task.workflow_template."source" IS '来源 (builtin:内置:gray/owner:主人自建:blue/agent:分身生成:violet/marketplace:市场物化:green)';
COMMENT ON COLUMN hasn_task.workflow_template."market_ref" IS '市场发布物溯源 {market_template_id}@{version}（非市场来源 NULL）；上架态/定价不在本表';
COMMENT ON COLUMN hasn_task.workflow_template."sku_ref" IS '官方付费模板的 MK offering 挂钩（对齐 hasn_app_catalog.sku_ref）；NULL=免费，仅 builtin 行用';
COMMENT ON COLUMN hasn_task.workflow_template."version" IS '模板版本（升级不影响在跑实例——实例化即物化节点行，天然快照）';

-- ============================================================
-- 2. hasn_task.workflow 加溯源列 template_key（实例来自哪个模板）
--    goal 列已在基表 2026-06-11-workflow.sql 存在 → 跳过（IF NOT EXISTS 兜底幂等）。
-- ============================================================
ALTER TABLE hasn_task.workflow
    ADD COLUMN IF NOT EXISTS "template_key" varchar(64);
ALTER TABLE hasn_task.workflow
    ADD COLUMN IF NOT EXISTS "goal" text;

COMMENT ON COLUMN hasn_task.workflow."template_key" IS '溯源：实例来自哪个 workflow_template（手工编排的图为 NULL）';

-- ============================================================
-- 3. 系统字典 workflow_template_domain（领域分组显示元数据 · 组名/图标/色）
--    格式 value:label:color（doc11 §4.2）；icon 存 remark（sys_dict_data 无 icon 列）。
--    accent 色对齐 doc94 §8.4：startup=blue / finance=teal / office=indigo / professional=rose。
-- ============================================================
INSERT INTO sys_dict_type (name, code, remark, created_time, updated_time)
VALUES ('工作流场景领域', 'workflow_template_domain', '工作流模板领域分组（场景皮肤显示元数据：组名/图标/色）', NOW(), NULL)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, remark = EXCLUDED.remark, updated_time = NOW();

DO $$
DECLARE
    v_dict_type_id INTEGER;
BEGIN
    SELECT id INTO v_dict_type_id FROM sys_dict_type
    WHERE code = 'workflow_template_domain' ORDER BY id DESC LIMIT 1;

    -- startup 个人创业（accent blue / icon rocket）
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'workflow_template_domain' AND value = 'startup') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('workflow_template_domain', '个人创业', 'startup', 'blue', 1, 1, v_dict_type_id, 'rocket', NOW(), NULL);
    END IF;
    -- finance 金融投研（accent teal / icon trending-up）
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'workflow_template_domain' AND value = 'finance') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('workflow_template_domain', '金融投研', 'finance', 'teal', 2, 1, v_dict_type_id, 'trending-up', NOW(), NULL);
    END IF;
    -- office 企业办公（accent indigo / icon building）
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'workflow_template_domain' AND value = 'office') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('workflow_template_domain', '企业办公', 'office', 'indigo', 3, 1, v_dict_type_id, 'building', NOW(), NULL);
    END IF;
    -- professional 专业服务（accent rose / icon scale）
    IF NOT EXISTS (SELECT 1 FROM sys_dict_data WHERE type_code = 'workflow_template_domain' AND value = 'professional') THEN
        INSERT INTO sys_dict_data (type_code, label, value, color, sort, status, type_id, remark, created_time, updated_time)
        VALUES ('workflow_template_domain', '专业服务', 'professional', 'rose', 4, 1, v_dict_type_id, 'scale', NOW(), NULL);
    END IF;
END $$;
