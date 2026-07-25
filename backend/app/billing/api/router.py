"""billing 模块路由注册（支付 pay + 订阅积分 user_tier 合并）

本模块由 `app/pay` + `app/user_tier` 按 ADR-15 §4 合并为单一 AI-Native 应用 `billing`。
**URL 前缀保持不变**（daemon `domains/billing/cloud.rs::BillingCloud` 代理路径依赖它）：

- 支付 `/api/v1/pay/*`：admin（JWT+RBAC）/ app（JWT）/ open（无认证，回调）
- 订阅积分 `/api/v1/user_tier/*`：admin / app / open（定价页）/ agent（X-Agent-Key）

对外导出 7 个 APIRouter：pay_v1/pay_app/pay_open + user_tier_v1/user_tier_app/user_tier_open/user_tier_agent。
"""

from fastapi import APIRouter

from backend.app.billing.api.v1.admin.billing_offering import router as admin_offering_router
from backend.app.billing.api.v1.admin.billing_plan import router as admin_plan_router
from backend.app.billing.api.v1.admin.channel import router as admin_channel_router
from backend.app.billing.api.v1.admin.contract import router as admin_contract_router

# ── 支付 admin（JWT + RBAC）──
from backend.app.billing.api.v1.admin.merchant import router as admin_merchant_router

# admin.rate（模型积分费率 model_credit_rate，D3）随自建 LLM 网关删除（2026-06-15 new-api 解耦）。
from backend.app.billing.api.v1.admin.newapi_quota import router as admin_newapi_quota_router
from backend.app.billing.api.v1.admin.notify_log import router as admin_notify_log_router
from backend.app.billing.api.v1.admin.order import router as admin_order_router

# ── 订阅积分 admin（JWT + RBAC）──
from backend.app.billing.api.v1.admin.subscription import router as admin_subscription_router
from backend.app.billing.api.v1.agent.quota import router as agent_quota_router
from backend.app.billing.api.v1.agent.subscription import router as agent_subscription_router
from backend.app.billing.api.v1.agent.usage import router as agent_usage_router

# ── 支付 app / open ──
from backend.app.billing.api.v1.app.pay import router as app_pay_router

# ── 订阅积分 app / open / agent ──
# 统一商业化内核（MK-7）：费用与账单中心聚合读 + 试用发放
from backend.app.billing.api.v1.app.center import router as app_center_router
from backend.app.billing.api.v1.app.subscription import router as app_subscription_router
from backend.app.billing.api.v1.open.notify import router as open_notify_router
from backend.app.billing.api.v1.open.pricing import router as open_pricing_router
from backend.core.conf import settings

# ========================================
# 支付 API（/api/v1/pay/*）
# ========================================
pay_v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/pay', tags=['支付管理'])

pay_v1.include_router(admin_merchant_router, prefix='/merchants', tags=['支付管理-商户'])
pay_v1.include_router(admin_channel_router, prefix='/channels', tags=['支付管理-渠道'])
pay_v1.include_router(admin_order_router, prefix='/orders', tags=['支付管理-订单'])
pay_v1.include_router(admin_contract_router, prefix='/contracts', tags=['支付管理-签约'])
pay_v1.include_router(admin_notify_log_router, prefix='/notify-logs', tags=['支付管理-回调日志'])

pay_app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/pay/app', tags=['支付-用户端'])
pay_app.include_router(app_pay_router, tags=['支付-用户端'])

pay_open = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/pay/open', tags=['支付-公开'])
pay_open.include_router(open_notify_router, tags=['支付-回调'])


# ========================================
# 订阅积分 API（/api/v1/user_tier/*）
# ========================================
user_tier_v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/user_tier', tags=['订阅积分-管理'])

user_tier_v1.include_router(admin_subscription_router, prefix='/subscriptions', tags=['管理-用户订阅'])
# /balances、/transactions、/packages、/tiers 四个 admin 面随 doc94 D1 删除：
# 余额与流水的权威在 NewAPI；档位与积分包的管理面是商业化中心的 /offerings + /plans。
# /rates（模型积分费率 model_credit_rate，D3）随自建 LLM 网关删除（2026-06-15 new-api 解耦）。
user_tier_v1.include_router(admin_newapi_quota_router, prefix='/newapi-quota', tags=['管理-Token额度用量'])
# 统一商业化内核（MK-6）：商品目录 offering + 价格档位 plan 管理面（商业化中心）
user_tier_v1.include_router(admin_offering_router, prefix='/offerings', tags=['管理-商品目录（商业化内核）'])
user_tier_v1.include_router(admin_plan_router, prefix='/plans', tags=['管理-价格档位（商业化内核）'])

user_tier_app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/user_tier/app', tags=['订阅积分-用户端'])
user_tier_app.include_router(app_subscription_router, prefix='/subscription', tags=['用户-订阅管理'])
# 统一商业化内核（MK-7）：费用与账单中心（/api/v1/user_tier/app/center）
user_tier_app.include_router(app_center_router, prefix='/center', tags=['用户-费用与账单中心'])

user_tier_open = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/user_tier/open', tags=['订阅积分-公开'])
user_tier_open.include_router(open_pricing_router, tags=['公开-定价信息'])

user_tier_agent = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/user_tier/agent', tags=['订阅积分-Agent'])
user_tier_agent.include_router(agent_quota_router, prefix='/quota', tags=['Agent-配额查询'])
user_tier_agent.include_router(agent_subscription_router, prefix='/subscriptions', tags=['Agent-订阅查询'])
user_tier_agent.include_router(agent_usage_router, prefix='/usage', tags=['Agent-用量统计'])
