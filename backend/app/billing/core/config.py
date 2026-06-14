"""支付模块配置"""

import os

# 支付回调基础 URL — 从环境变量读取，默认本地开发
PAY_ORDER_NOTIFY_URL: str = os.getenv(
    'PAY_ORDER_NOTIFY_URL',
    'https://api.huanxing.dcfuture.cn/api/v1/pay/open/notify',
)

PAY_REFUND_NOTIFY_URL: str = os.getenv(
    'PAY_REFUND_NOTIFY_URL',
    'https://api.huanxing.dcfuture.cn/api/v1/pay/open/refund-notify',
)

PAY_CONTRACT_NOTIFY_URL: str = os.getenv(
    'PAY_CONTRACT_NOTIFY_URL',
    'https://api.huanxing.dcfuture.cn/api/v1/pay/open/contract-notify',
)

# 订单过期时间（分钟）
ORDER_EXPIRE_MINUTES: int = 30

# 注意：套餐价格已从数据库 subscription_tier 表读取，不再硬编码
# 数据库字段：tier_name, display_name, monthly_price, yearly_price, app_code
