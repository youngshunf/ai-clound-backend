-- 003: URL 级去重登记表（doc08 §4.4 三层去重之 URL 级；阶段一 ②）
-- 平台级（不分 user）：采集抓取前用规范化 URL 查重，命中且在窗口期内则跳过抓取，
-- 防重复烧 firecrawl 成本。同时承载众包线索池的"已抓 URL 池"语义（命中计数/产线索量飞轮度量）。
-- PostgreSQL 语法；落 schema hasn_growth。codegen: --app hasn_growth --schema hasn_growth。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS crawled_url (
    id bigserial PRIMARY KEY,
    url_hash varchar(64) NOT NULL,
    normalized_url varchar(2048) NOT NULL,
    domain varchar(255),
    source_type varchar(32),
    crawl_count int NOT NULL DEFAULT 0,
    hit_count int NOT NULL DEFAULT 0,
    lead_yield int NOT NULL DEFAULT 0,
    last_outcome varchar(16) NOT NULL DEFAULT 'pending',
    last_job_id bigint,
    first_crawled_at timestamptz,
    last_crawled_at timestamptz,
    meta_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_crawled_url_hash UNIQUE (url_hash)
);
COMMENT ON TABLE crawled_url IS 'URL 级去重登记表（平台级·抓前查重防重复爬·doc08 §4.4）';
COMMENT ON COLUMN crawled_url.url_hash IS '规范化 URL 的 sha256（唯一去重键）';
COMMENT ON COLUMN crawled_url.normalized_url IS '规范化 URL（小写host/去www/去fragment/去追踪参数/query排序）';
COMMENT ON COLUMN crawled_url.domain IS '域名（按域统计/限频）';
COMMENT ON COLUMN crawled_url.source_type IS '首次抓取的数据源类型';
COMMENT ON COLUMN crawled_url.crawl_count IS '实际抓取次数（过期重抓累加）';
COMMENT ON COLUMN crawled_url.hit_count IS '抓前查重命中次数（被跳过次数·省成本度量）';
COMMENT ON COLUMN crawled_url.lead_yield IS '该 URL 累计产出有效线索数';
COMMENT ON COLUMN crawled_url.last_outcome IS '最近抓取结果 (pending:待抓:gray/succeeded:成功:green/empty:无内容:orange/failed:失败:red)';
COMMENT ON COLUMN crawled_url.last_job_id IS '最近抓取所属采集任务 id';
CREATE INDEX IF NOT EXISTS idx_growth_crawled_url_domain ON crawled_url (domain);
CREATE INDEX IF NOT EXISTS idx_growth_crawled_url_last ON crawled_url (last_crawled_at);
