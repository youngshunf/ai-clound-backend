-- ================================================
-- 唤星支付模块 - 数据库迁移脚本 v1.0
-- 日期: 2026-03-01
-- 新增: pay_app, pay_channel, pay_order, pay_notify_log, pay_refund, pay_contract
-- 修改: subscription_tier 数据更新为星辰系列
-- ================================================

-- 1. 支付应用配置表
CREATE TABLE IF NOT EXISTS pay_app (
    id              BIGSERIAL PRIMARY KEY,
    app_key         VARCHAR(64)     NOT NULL UNIQUE,
    name            VARCHAR(64)     NOT NULL,
    order_notify_url VARCHAR(1024)  NOT NULL,
    status          SMALLINT        NOT NULL DEFAULT 1,
    remark          VARCHAR(255),
    refund_notify_url VARCHAR(1024),
    created_time    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_time    TIMESTAMPTZ
);
COMMENT ON TABLE pay_app IS '支付应用配置表';

-- 2. 支付渠道配置表
CREATE TABLE IF NOT EXISTS pay_channel (
    id              BIGSERIAL PRIMARY KEY,
    app_id          BIGINT          NOT NULL REFERENCES pay_app(id),
    code            VARCHAR(32)     NOT NULL,
    name            VARCHAR(64)     NOT NULL,
    status          SMALLINT        NOT NULL DEFAULT 1,
    fee_rate        NUMERIC(6,4)    NOT NULL DEFAULT 0,
    remark          VARCHAR(255),
    config          JSONB           NOT NULL DEFAULT '{}',
    created_time    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_time    TIMESTAMPTZ,
    CONSTRAINT uq_pay_channel_app_code UNIQUE (app_id, code)
);
COMMENT ON TABLE pay_channel IS '支付渠道配置表';

-- 3. 支付订单表
CREATE TABLE IF NOT EXISTS pay_order (
    id              BIGSERIAL PRIMARY KEY,
    order_no        VARCHAR(64)     NOT NULL UNIQUE,
    user_id         BIGINT          NOT NULL,
    app_id          BIGINT          NOT NULL REFERENCES pay_app(id),
    order_type      VARCHAR(32)     NOT NULL,
    subject         VARCHAR(128)    NOT NULL,
    amount          BIGINT          NOT NULL,
    pay_amount      BIGINT          NOT NULL,
    expire_time     TIMESTAMPTZ     NOT NULL,
    channel_id      BIGINT          REFERENCES pay_channel(id),
    channel_code    VARCHAR(32),
    body            VARCHAR(256),
    target_tier     VARCHAR(32),
    billing_cycle   VARCHAR(16),
    discount_amount BIGINT          NOT NULL DEFAULT 0,
    refund_amount   BIGINT          NOT NULL DEFAULT 0,
    status          SMALLINT        NOT NULL DEFAULT 0,
    user_ip         VARCHAR(50),
    channel_order_no VARCHAR(128),
    channel_user_id VARCHAR(128),
    success_time    TIMESTAMPTZ,
    extra_data      JSONB,
    created_time    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_time    TIMESTAMPTZ
);
COMMENT ON TABLE pay_order IS '支付订单表';
CREATE INDEX IF NOT EXISTS idx_pay_order_user_id ON pay_order(user_id);
CREATE INDEX IF NOT EXISTS idx_pay_order_status ON pay_order(status);
CREATE INDEX IF NOT EXISTS idx_pay_order_channel_no ON pay_order(channel_order_no);
CREATE INDEX IF NOT EXISTS idx_pay_order_created ON pay_order(created_time);

-- 4. 支付回调日志表
CREATE TABLE IF NOT EXISTS pay_notify_log (
    id              BIGSERIAL PRIMARY KEY,
    notify_type     VARCHAR(16)     NOT NULL,
    order_no        VARCHAR(64),
    channel_code    VARCHAR(32),
    notify_data     TEXT,
    status          SMALLINT        NOT NULL DEFAULT 0,
    error_msg       VARCHAR(512),
    created_time    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_time    TIMESTAMPTZ
);
COMMENT ON TABLE pay_notify_log IS '支付回调日志表';

-- 5. 退款记录表
CREATE TABLE IF NOT EXISTS pay_refund (
    id              BIGSERIAL PRIMARY KEY,
    refund_no       VARCHAR(64)     NOT NULL UNIQUE,
    order_no        VARCHAR(64)     NOT NULL,
    user_id         BIGINT          NOT NULL,
    refund_amount   BIGINT          NOT NULL,
    channel_code    VARCHAR(32),
    reason          VARCHAR(256),
    channel_refund_no VARCHAR(128),
    status          SMALLINT        NOT NULL DEFAULT 0,
    success_time    TIMESTAMPTZ,
    created_time    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_time    TIMESTAMPTZ
);
COMMENT ON TABLE pay_refund IS '退款记录表';

-- 6. 签约记录表（自动续费核心）
CREATE TABLE IF NOT EXISTS pay_contract (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          NOT NULL,
    app_id          BIGINT          NOT NULL REFERENCES pay_app(id),
    channel_code    VARCHAR(32)     NOT NULL,
    contract_no     VARCHAR(128)    NOT NULL UNIQUE,
    tier            VARCHAR(32)     NOT NULL,
    billing_cycle   VARCHAR(16)     NOT NULL,
    deduct_amount   BIGINT          NOT NULL,
    channel_contract_id VARCHAR(128),
    plan_id         VARCHAR(64),
    status          SMALLINT        NOT NULL DEFAULT 0,
    signed_time     TIMESTAMPTZ,
    terminated_time TIMESTAMPTZ,
    terminate_reason VARCHAR(256),
    next_deduct_date DATE,
    last_deduct_time TIMESTAMPTZ,
    deduct_count    INTEGER         NOT NULL DEFAULT 0,
    extra_data      JSONB,
    created_time    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_time    TIMESTAMPTZ
);
COMMENT ON TABLE pay_contract IS '签约记录表（自动续费核心）';
CREATE INDEX IF NOT EXISTS idx_pay_contract_user ON pay_contract(user_id);
CREATE INDEX IF NOT EXISTS idx_pay_contract_status ON pay_contract(status);
CREATE INDEX IF NOT EXISTS idx_pay_contract_next_deduct ON pay_contract(next_deduct_date) WHERE status = 1;

-- 7. 更新 subscription_tier 数据为星辰系列
-- 注意：先清空再插入，如果已有用户数据需要做数据迁移
-- TRUNCATE subscription_tier RESTART IDENTITY;
-- 如果不想清空，使用 UPSERT：
INSERT INTO subscription_tier (app_code, tier_name, display_name, monthly_credits, monthly_price, yearly_price, yearly_discount, features, enabled, sort_order) VALUES
('huanxing', 'star_dust',  '星尘', 500,    0,   NULL,  NULL, '{"msg_per_day": 20, "memory": "7d", "model": "sonnet", "backup": false}', true, 0),
('huanxing', 'star_glow',  '星芒', 2000,  49,   470,   0.80, '{"msg_per_day": 100, "memory": "forever", "model": "sonnet", "backup": "git"}', true, 1),
('huanxing', 'star_shine', '星辰', 5000,  99,   950,   0.80, '{"msg_per_day": 200, "memory": "forever", "model": "opus", "backup": "git", "advanced_skills": true}', true, 2),
('huanxing', 'star_glory', '星耀', 15000, 299,  2870,  0.80, '{"msg_per_day": -1, "memory": "forever", "model": "opus", "backup": "git+cloud", "dedicated": true, "vip_support": true}', true, 3)
ON CONFLICT (app_code, tier_name) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    monthly_credits = EXCLUDED.monthly_credits,
    monthly_price = EXCLUDED.monthly_price,
    yearly_price = EXCLUDED.yearly_price,
    yearly_discount = EXCLUDED.yearly_discount,
    features = EXCLUDED.features,
    sort_order = EXCLUDED.sort_order;

-- 8. 初始化默认支付应用
INSERT INTO pay_app (app_key, name, order_notify_url, refund_notify_url) VALUES
('huanxing', '唤星AI', 'https://huanxing.ai.dcfuture.cn/api/v1/huanxing/open/pay/notify/internal', 'https://huanxing.ai.dcfuture.cn/api/v1/huanxing/open/pay/notify/refund/internal')
ON CONFLICT (app_key) DO NOTHING;
