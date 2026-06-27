-- 005: 线索领取额度账本（doc93 阶段四 4.2 线索付费）
-- 用户「请求线索」前置额度闸（填实 2.1 的 _check_quota 接缝）：免费额度内放行，超额走支付购买。
-- 线索是**独立支付商品**，按支付订单结算，**不走 new-api 积分**（doc93 §4.2 line 165 铁律）。
-- 每用户一行（UNIQUE user_id）：免费额度按月重置（period_key 变更即归零 free_used），
-- 购买余额 purchased_balance 永不过期累计；purchased_total/consumed_total 仅作对账。
-- PostgreSQL 语法；落 schema hasn_growth。codegen: --app hasn_growth --schema hasn_growth（仅取 model）。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS lead_quota (
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    period_key varchar(7) NOT NULL DEFAULT '',
    free_used int NOT NULL DEFAULT 0,
    purchased_balance int NOT NULL DEFAULT 0,
    purchased_total int NOT NULL DEFAULT 0,
    consumed_total int NOT NULL DEFAULT 0,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_lead_quota_user UNIQUE (user_id)
);
COMMENT ON TABLE lead_quota IS '线索领取额度账本（doc93 §4.2：免费额度按月重置 + 购买余额永不过期·线索付费不走积分）';
COMMENT ON COLUMN lead_quota.user_id IS '用户 ID（每用户一行）';
COMMENT ON COLUMN lead_quota.period_key IS '免费额度归属月 YYYY-MM（与当前月不一致即归零 free_used）';
COMMENT ON COLUMN lead_quota.free_used IS '本月已用免费额度（领取的免费线索数）';
COMMENT ON COLUMN lead_quota.purchased_balance IS '购买的可领取线索余额（永不过期·支付到账增加·领取扣减）';
COMMENT ON COLUMN lead_quota.purchased_total IS '累计购买线索数（对账）';
COMMENT ON COLUMN lead_quota.consumed_total IS '累计已领取线索数（免费+购买·对账）';
