-- =====================================================
-- 分身运行位置：hasn_agents 补 runtime_location 列
-- 双形态 Runtime（设计 docs/hasn-node设计文档/08-云端Runtime托管/02）：
--   local  — 跑在主人电脑上（非沙箱，可访问授权目录，关机离线）；
--   cloud  — 跑在唤星服务器（Docker 沙箱隔离，关机仍在线）。
-- 创建分身时选择，云端为权威源；daemon read-through 镜像。存量行默认 local，
-- 零迁移、dispatch 缺值按 local。切换位置 = detach + 重新 bind（不做在线迁移）。
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "runtime_location" varchar(16) NOT NULL DEFAULT 'local';

COMMENT ON COLUMN "public"."hasn_agents"."runtime_location" IS '运行位置 (local:本地:blue/cloud:云端:green)';
