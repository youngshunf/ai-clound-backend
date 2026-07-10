-- 创作运营重构 S4：作品明细 work 表 + 竞品 works_count + content_stage 成片阶段字典
-- 设计事实源：docs/自媒体创作运营/01-创作运营重构设计…md §6.3（账号作品）+ §6.4（竞品作品）+ §7（数据模型）+ §8（工具面）。
-- 归并键（§7 评审补）：自己账号作品以 url/external_id 与 publish.published_url 关联，数据页以 publish 记录为主行、
--   work 抓取值为刷新源，避免两套数字打架。upsert 归并逻辑落 service（url/external_id 匹配则更新、否则插入）。
-- PostgreSQL 语法，落 schema hasn_creator。work 为只读内部表（仅 Agent 工具 works.upsert/list + owner 读消费，
--   非 admin CRUD 实体），按既有 codegen model 范式手工建 model（同 platform 先例，不跑全量 codegen）。

SET search_path TO hasn_creator, public;

-- ========== 新表 work：作品明细（自己账号 own / 竞品 competitor 共用，按 source_type 区分）==========
CREATE TABLE IF NOT EXISTS work (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL DEFAULT 0,
    user_id bigint NOT NULL DEFAULT 0,
    owner_scope varchar(16) NOT NULL DEFAULT '',
    enterprise_id bigint,
    assignee varchar(64),
    source_type varchar(16) NOT NULL DEFAULT 'own',
    account_id bigint,
    competitor_id bigint,
    platform varchar(50) NOT NULL DEFAULT '',
    external_id varchar(128),
    title text,
    url text,
    cover_uri text,
    published_at timestamptz,
    views int NOT NULL DEFAULT 0,
    likes int NOT NULL DEFAULT 0,
    comments int NOT NULL DEFAULT 0,
    shares int NOT NULL DEFAULT 0,
    favorites int NOT NULL DEFAULT 0,
    collected_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE work IS '作品明细：自己账号(own)/竞品(competitor)的单条作品 + 指标，按 source_type + *_id 区分归属';
COMMENT ON COLUMN work.source_type IS '来源 (own:自己账号:blue/competitor:竞品:orange)';
COMMENT ON COLUMN work.account_id IS '自己账号 ID（source_type=own 时非空，指向 account.id）';
COMMENT ON COLUMN work.competitor_id IS '竞品 ID（source_type=competitor 时非空，指向 competitor.id）';
COMMENT ON COLUMN work.platform IS '平台 key（冗余自账号/竞品，便于跨平台聚合展示）';
COMMENT ON COLUMN work.external_id IS '平台侧作品 ID（归并键之一：同一作品重复抓取按此归并；无则用 url）';
COMMENT ON COLUMN work.url IS '作品原链接（归并键之一：与 publish.published_url 关联，自己作品两路指标归并展示）';
COMMENT ON COLUMN work.cover_uri IS '封面 hasn://asset/ 引用（落私有桶）或平台原始封面 URL';
COMMENT ON COLUMN work.collected_at IS '抓取采集时刻（数据新鲜度「更新于 T」诚实标注）';

-- 归属裁剪 + 作品列表按账号/竞品下钻的高频路径索引。
CREATE INDEX IF NOT EXISTS idx_creator_work_project ON work (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_work_account ON work (account_id) WHERE account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_creator_work_competitor ON work (competitor_id) WHERE competitor_id IS NOT NULL;

-- ========== 加列 competitor.works_count（作品数，§7）==========
ALTER TABLE competitor ADD COLUMN IF NOT EXISTS works_count int NOT NULL DEFAULT 0;
COMMENT ON COLUMN competitor.works_count IS '作品数（分身调研回填；工具层 researched=true 时必填）';

-- ========== content_stage 阶段字典补 final_video（成片，§7 评审补）==========
-- stage 是 String(30) 无 DB 枚举，改动仅字典注释（工具 schema/校验 + webui 渲染在代码侧）。
COMMENT ON COLUMN content_stage.stage IS '阶段 (research:调研:blue/outline:大纲:cyan/first_draft:初稿:orange/final_draft:终稿:purple/cover:封面:green/storyboard:分镜:teal/voiceover:口播:violet/final_video:成片:red)';
