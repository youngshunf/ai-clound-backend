"""客户端 companion 应用（hasn_client）ORM 模型.

推送 / 遥测 / 灰度 / owner api key 等全体客户端共用能力的数据表。
ADR-15 收编 R2：由历史扁平 `backend/app/models/` 迁入标准 `app/hasn_client/model/`。
"""
from backend.app.hasn_client.model.push_token import PushChannel as PushChannel
from backend.app.hasn_client.model.push_token import PushToken as PushToken
from backend.app.hasn_client.model.push_token_audit import PushTokenAudit as PushTokenAudit
