# 支付（原 app/pay）
from backend.app.billing.model.pay_app import PayApp as PayApp
from backend.app.billing.model.pay_channel import PayChannel as PayChannel
from backend.app.billing.model.pay_contract import PayContract as PayContract
from backend.app.billing.model.pay_merchant import PayMerchant as PayMerchant
from backend.app.billing.model.pay_notify_log import PayNotifyLog as PayNotifyLog
from backend.app.billing.model.pay_order import PayOrder as PayOrder
from backend.app.billing.model.pay_refund import PayRefund as PayRefund

# 订阅积分（原 app/user_tier）
# model_credit_rate（D3）随自建 LLM 网关删除（2026-06-15 new-api 解耦）。
# subscription_tier / credit_package / user_credit_balance / credit_transaction 随 doc94 D1 删除：
# 档位事实源迁到 billing_offering + billing_plan，余额与流水的唯一权威是 NewAPI。
from backend.app.billing.model.user_subscription import UserSubscription as UserSubscription
from backend.app.billing.model.billing_offering import BillingOffering as BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan as BillingPlan
from backend.app.billing.model.credit_grant_event import CreditGrantEvent as CreditGrantEvent
