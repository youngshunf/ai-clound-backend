from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CreditGrantEventSchemaBase(SchemaBase):
    """履约事件表（事务 outbox + 云端审计，不保存权威余额）基础模型"""
    event_id: str = Field(description='投递给 NewAPI 的 event_id（UUID 字符串，全局唯一；超时重投必须复用同一个，禁止换 ID 重发）')
    idempotency_key: str = Field(description='业务幂等键（取自固定全集，不得现场自创）')
    event_type: str = Field(description='事件类型 (wallet_grant:钱包发放:green/wallet_revoke:钱包回收:orange/subscription_activate:订阅生效:blue/subscription_expire:订阅到期:grey)')
    app_code: str = Field(description='应用标识')
    user_id: int = Field(description='唤星用户 ID')
    newapi_user_id: int = Field(description='履约目标 NewAPI 用户 ID（快照）')
    order_no: str | None = Field(None, description='关联支付订单号')
    refund_no: str | None = Field(None, description='关联退款单号')
    subscription_id: int | None = Field(None, description='关联订阅合同主键')
    contract_no: str | None = Field(None, description='关联订阅合同号')
    credit_amount: Decimal | None = Field(None, description='不可变的发放/回收参数积分数（不是余额）')
    applied_credits: Decimal | None = Field(None, description='NewAPI 回执的实际入账/回收积分（审计以此为准）')
    payload: dict = Field(description='投递给 NewAPI 的请求快照')
    payload_hash: str = Field(description='投递载荷指纹，用于冲突检测')
    status: str = Field(description='状态 (pending:待投递:blue/processing:投递中:orange/succeeded:已完成:green/retrying:重试中:orange/dead:死信:red/cancelled:已取消:grey)')
    attempt_count: int = Field(description='已投递尝试次数')
    next_attempt_at: datetime | None = Field(None, description='下次投递时间（指数退避 + 抖动）')
    last_error_code: str | None = Field(None, description='最后一次失败的机器错误码')
    last_error_message: str | None = Field(None, description='最后一次失败原因（敏感值已脱敏）')
    response_snapshot: dict | None = Field(None, description='NewAPI 回执快照，仅供排障，不得用于判余额')
    delivered_at: datetime | None = Field(None, description='投递成功时间')


class CreateCreditGrantEventParam(CreditGrantEventSchemaBase):
    """创建履约事件表（事务 outbox + 云端审计，不保存权威余额）参数"""


class UpdateCreditGrantEventParam(CreditGrantEventSchemaBase):
    """更新履约事件表（事务 outbox + 云端审计，不保存权威余额）参数"""


class DeleteCreditGrantEventParam(SchemaBase):
    """删除履约事件表（事务 outbox + 云端审计，不保存权威余额）参数"""

    pks: list[int] = Field(description='履约事件表（事务 outbox + 云端审计，不保存权威余额） ID 列表')


class GetCreditGrantEventDetail(CreditGrantEventSchemaBase):
    """履约事件表（事务 outbox + 云端审计，不保存权威余额）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
