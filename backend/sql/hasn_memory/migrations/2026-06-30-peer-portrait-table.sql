-- =====================================================
-- HASN 记忆系统 - peer_portrait 权威表（doc17 PEERSYN-P1 · G1）
-- =====================================================
-- Peer 画像云端权威表：按 (owner_id, peer_hasn_id) 唯一（owner 视角唯一，跨该 owner 名下
-- 全部分身对同一对方的观察合并成一份）。镜像本地 crate hasn-memory 的 peer_portraits 表字段
-- （epoch ms 时间戳、复合主键），但**不含** revision/synced_at/is_dirty（那是本地 SQLite 的
-- 同步簿记，云端权威无需）；下行同步经 hasn_sync_events(namespace='portraits') → daemon
-- MemorySyncPullApplier::apply_peer_portrait 落本地镜像。
--
-- ⚠️ 幂等：CREATE TABLE IF NOT EXISTS + 全 CHECK/INDEX 幂等，重复执行安全。放在 migrations/
-- 目录下由 run_pending_migrations 扫描执行（bootstrap 目录顶层 *.sql 不被 runner 扫描）。
-- hasn_memory schema 已由 2026-06-15-move-memory-tables 迁移建立。
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_memory";

CREATE TABLE IF NOT EXISTS "hasn_memory"."peer_portrait" (
  "owner_id"            varchar(40)  NOT NULL,
  "peer_hasn_id"        varchar(40)  NOT NULL,
  "peer_kind"           varchar(16)  NOT NULL DEFAULT 'human',
  "portrait_text"       text         NOT NULL DEFAULT '',
  "language"            varchar(8)   NOT NULL DEFAULT 'zh',
  "version"             bigint       NOT NULL DEFAULT 1,
  "revised_by"          varchar(40)  NOT NULL DEFAULT 'system',
  "source_fact_count"   integer      NOT NULL DEFAULT 0,
  "last_synthesized_at" bigint,
  "last_interaction_at" bigint,
  "token_count"         integer      NOT NULL DEFAULT 0,
  "created_at"          bigint       NOT NULL,
  "updated_at"          bigint       NOT NULL,
  CONSTRAINT "pk_peer_portrait" PRIMARY KEY ("owner_id", "peer_hasn_id"),
  CONSTRAINT "ck_peer_portrait_peer_kind" CHECK ("peer_kind" IN ('human', 'agent'))
);

CREATE INDEX IF NOT EXISTS "idx_peer_portrait_owner"
  ON "hasn_memory"."peer_portrait" ("owner_id", "updated_at" DESC);

COMMENT ON TABLE "hasn_memory"."peer_portrait"
  IS 'HASN 记忆系统 - Peer 画像（owner 视角唯一，跨分身合并；下行 namespace=portraits）';
