-- =====================================================
-- B0'：支付宝当面付（扫码）渠道 alipay_qr（桌面端订阅与积分计费 §4.2）
-- =====================================================
-- 桌面端「全部应用内二维码」决策：支付宝走当面付 trade.precreate 出可扫二维码。
-- 复用既有支付宝商户凭据（与 alipay_pc 同 merchant），渠道 code='alipay_qr'，
-- service 层 _build_client 据 code 路由到 AlipayQrClient（precreate）。
--
-- 幂等：按 code 唯一约束 upsert。merchant_id 取既有支付宝商户（alipay_pc 的 merchant）。
-- =====================================================

INSERT INTO pay_channel (code, name, merchant_id, status, fee_rate, remark, created_time)
SELECT
    'alipay_qr',
    '支付宝扫码支付',
    (SELECT merchant_id FROM pay_channel WHERE code = 'alipay_pc' LIMIT 1),
    1,
    0,
    '支付宝当面付 trade.precreate，桌面端应用内二维码',
    NOW()
WHERE NOT EXISTS (SELECT 1 FROM pay_channel WHERE code = 'alipay_qr');
