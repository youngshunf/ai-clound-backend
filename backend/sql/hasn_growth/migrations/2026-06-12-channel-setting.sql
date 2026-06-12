-- 迁移：hasn_growth.channel_setting 主人渠道设置（M6 发送 worker J1 闸门：微信自动发送确认）。
--
-- 背景：M6 发送 worker 扫 approved 触达自动分发。微信渠道是 J1 高风险闸门——owner 必须在
-- 渠道设置显式打开 wechat_auto_send_confirmed=true（M7 UI 开关 + 二次确认）worker 才允许自动发，
-- 否则恒走 manual_assist（owner 手动复制发送）。本表存该 owner 级开关；缺省（无行）= 未确认 = 安全默认关。
--
-- 幂等：表不存在才建。后续 M7 经 fba codegen 补 model/schema/api（owner 端读写开关）。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS hasn_growth.channel_setting (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    wechat_auto_send_confirmed boolean NOT NULL DEFAULT false,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_channel_setting_user UNIQUE (user_id)
);
COMMENT ON TABLE hasn_growth.channel_setting IS '获客主人渠道设置（M6 发送 worker J1 闸门来源）';
COMMENT ON COLUMN hasn_growth.channel_setting.wechat_auto_send_confirmed IS '微信自动发送已确认（J1 高风险闸门；缺省 false=未确认=worker 不自动发微信，恒 manual_assist）';
