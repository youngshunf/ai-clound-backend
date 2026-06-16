-- =====================================================
-- 退役 openclaw_gateway_configs（public.openclaw_gateway_configs）
--
-- 背景：早期手机登录响应里塞了一个 gateway_token（由 create_gateway_config 每次登录
--   写/取一行 openclaw_gateway_configs），设计意图是给 ZeroClaw sidecar/Openclaw 网关
--   做认证票据。但下游从未真正消费——桌面端 hasn-node 的 PhoneVerifyResponse 结构体
--   根本没有 gateway_token 字段（serde 忽略未知字段），全仓 grep 零引用。本次随
--   app/openclaw 模块（model/schema/service/api + router 接线）一并退役，
--   phone-login 响应去掉 gateway_token 字段（PhoneLoginResponse 同步删字段）。
--   纯云端改动，hasn-node 零改。
--
-- ⚠️ 生产执行须经福仔停机窗口授权（与其它 schema 切换同一约定）。本机 dev 库已先行 DROP 验证。
--
-- 幂等：IF EXISTS + CASCADE（确认全库无外键指向后再放行；CASCADE 仅兜底）。
-- =====================================================

DROP TABLE IF EXISTS "public"."openclaw_gateway_configs" CASCADE;
