-- =====================================================
-- 多设备登录与设备管理：hasn_nodes 补「客户端 IP + 归属地」两列
-- 用途：设备管理页展示每台登录设备的 IP 与归属地（城市/地区/ISP）。
--   ip_address   最近一次 WS 连接的客户端 IP（v4/v6），由 ws_node 注册时从
--                WS scope / X-Forwarded-For 抓取。
--   ip_location  GeoLite2 离线库解析的归属地字符串；**零 Mock**：mmdb 缺失或
--                私网/无法解析时留空（NULL），UI 端如实显示「未知归属地」，
--                绝不伪造城市。
-- 存量行两列为空，下次该设备连接时由 ws_node 回填。
-- 设计事实源：docs/hasn-node设计文档/多设备登录与跨设备消息路由/00-设计总览.md §4
-- =====================================================

ALTER TABLE "public"."hasn_nodes"
  ADD COLUMN IF NOT EXISTS "ip_address" varchar(64),
  ADD COLUMN IF NOT EXISTS "ip_location" varchar(128);

COMMENT ON COLUMN "public"."hasn_nodes"."ip_address" IS '最近一次连接的客户端 IP（v4/v6）';
COMMENT ON COLUMN "public"."hasn_nodes"."ip_location" IS 'IP 归属地（GeoLite2 离线解析，缺库时留空表示未知）';
