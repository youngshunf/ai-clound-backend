-- =====================================================
-- 素材站目录 · 建表补齐 + 内置三站种子（PostgreSQL，幂等）
--
-- 【为什么会有这个文件】
-- A-P2-0（2026-07-12）把建表与种子写在 `backend/sql/hasn_stock/hasn_stock_providers.sql`，
-- 但生产迁移 runner（`run_pending_migrations.sh`）只扫 `find "$SQL_ROOT" -path "*/migrations/*.sql"`
-- ——**不在 migrations/ 子目录下的 SQL 文件永远不会被执行**。于是生产上：
--   · 表被启动期 `metadata.create_all` 建了出来（只建表、不种数据、不建模型未声明的索引）；
--   · 种子三行从未执行 → `hasn_stock.hasn_stock_providers` 长期 0 行；
--   · `cached_source_enum()` 缓存命中但 enabled 集合为空 → source enum 渲染成 `[]`；
--   · `hasn.stock.search` 报「没有支持 image/video 的已启用素材站，请在后台配置」。
-- 本文件把同一份建表 + 种子搬进 migrations/，使其真正纳入部署流程。
--
-- 【为什么不能直接照搬原文件】生产现存表是 `create_all` 建的，与原 SQL 的 DDL 有三处差异，
-- 直接跑原种子会失败：
--   ① 缺 provider 唯一索引 → `ON CONFLICT (provider)` 报 42P10（no unique or exclusion constraint）；
--   ② 缺 created_time 默认值（NOT NULL 无 DEFAULT）→ 原 INSERT 不带该列，报非空约束违反；
--   ③ 缺 enabled/priority/media_types/download_domains/display_name 的服务端默认值。
-- 因此本文件先补齐索引与默认值，再种子——对全新库与存量库都收敛到同一形状。
--
-- 事实源：docs/Agent产物系统/01-分身资源检索与素材站工具设计.md §4.5
-- =====================================================

CREATE SCHEMA IF NOT EXISTS hasn_stock;

-- 1) 全新库：完整建表（存量库为 no-op）
CREATE TABLE IF NOT EXISTS hasn_stock.hasn_stock_providers (
    id                bigserial      PRIMARY KEY,
    provider          varchar(40)    NOT NULL,
    display_name      varchar(80)    NOT NULL DEFAULT '',
    media_types       jsonb          NOT NULL DEFAULT '[]'::jsonb,
    api_key_cipher    text,
    download_domains  jsonb          NOT NULL DEFAULT '[]'::jsonb,
    enabled           boolean        NOT NULL DEFAULT true,
    priority          integer        NOT NULL DEFAULT 100,
    license_terms_url varchar(500),
    remark            varchar(255),
    created_time      timestamptz(6) NOT NULL DEFAULT now(),
    updated_time      timestamptz(6)
);

-- 2) 存量库（create_all 建出来的表）：补齐服务端默认值，与上面的 DDL 对齐。
--    create_all 只把默认值放在 Python 侧（model 的 default=），裸 SQL 写入拿不到，
--    其中 created_time 缺默认值会直接让种子 INSERT 失败。
ALTER TABLE hasn_stock.hasn_stock_providers ALTER COLUMN display_name     SET DEFAULT '';
ALTER TABLE hasn_stock.hasn_stock_providers ALTER COLUMN media_types      SET DEFAULT '[]'::jsonb;
ALTER TABLE hasn_stock.hasn_stock_providers ALTER COLUMN download_domains SET DEFAULT '[]'::jsonb;
ALTER TABLE hasn_stock.hasn_stock_providers ALTER COLUMN enabled          SET DEFAULT true;
ALTER TABLE hasn_stock.hasn_stock_providers ALTER COLUMN priority         SET DEFAULT 100;
ALTER TABLE hasn_stock.hasn_stock_providers ALTER COLUMN created_time     SET DEFAULT now();

-- 3) provider 唯一索引：既是业务约束（一个素材站一行），也是下面 ON CONFLICT 的前置。
--    create_all 不会建它（model 未声明），存量库必须在此补上。
CREATE UNIQUE INDEX IF NOT EXISTS uq_hasn_stock_providers_provider
    ON hasn_stock.hasn_stock_providers (provider);

-- 4) 默认 failover 链常用查询：enabled + priority 升序
CREATE INDEX IF NOT EXISTS ix_hasn_stock_providers_enabled_priority
    ON hasn_stock.hasn_stock_providers (enabled, priority);

COMMENT ON TABLE  hasn_stock.hasn_stock_providers IS '素材站目录（后台可配：provider/media_types/api_key加密/download_domains/enabled/priority）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.provider IS '素材站唯一标识（pexels/pixabay/coverr/…）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.display_name IS '展示名';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.media_types IS '支持的媒体类型 JSON 数组（image/video 子集，如 coverr 仅 ["video"]）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.api_key_cipher IS 'api_key 密文（KeyEncryption Fernet 加密；明文绝不回显/入日志/进 PDC；未配为 NULL）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.download_domains IS '该站下载直链合法域名 JSON 数组（驱动 stock.download SSRF 白名单）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.enabled IS '启用开关';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.priority IS '默认 failover 顺序（升序在前）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.license_terms_url IS '许可条款链接（随下载 meta_data 落库，供发布前合规审查）';
COMMENT ON COLUMN hasn_stock.hasn_stock_providers.remark IS '备注';

-- 5) 种子内置三行。
--    ⚠️ api_key 留空是**有意的**：密钥属于凭据，绝不入版本库。种完这三行后
--    `source` enum 会恢复成 ['pexels','pixabay','coverr']、failover 链不再为空，但真正搜到结果
--    还需管理员在后台「HASN / 素材站目录」为各站填 api_key（明文只进不出，Fernet 加密落库）。
--    未配 key 时 StockService 逐站抛「未配 api_key」并 warn 降级，全链失败才报错——不会静默假成功。
INSERT INTO hasn_stock.hasn_stock_providers
    (provider, display_name, media_types, download_domains, enabled, priority, license_terms_url)
VALUES
    ('pexels',  'Pexels',  '["image","video"]'::jsonb,
        '["images.pexels.com","videos.pexels.com","player.vimeo.com"]'::jsonb, true, 10,
        'https://www.pexels.com/license/'),
    ('pixabay', 'Pixabay', '["image","video"]'::jsonb,
        '["pixabay.com","cdn.pixabay.com"]'::jsonb, true, 20,
        'https://pixabay.com/service/license-summary/'),
    ('coverr',  'Coverr',  '["video"]'::jsonb,
        '["cdn.coverr.co"]'::jsonb, true, 30,
        'https://coverr.co/license')
ON CONFLICT (provider) DO NOTHING;
