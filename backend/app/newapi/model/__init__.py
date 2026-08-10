"""new-api 集成模块数据模型。"""

# 自建 API Key 模型物理上位于 apikey 子包（与其 crud/service/schema 同域）；在此显式导入，
# 确保 import app.newapi.model 时其 mapper 也随之注册进 MappedBase.metadata（建表/元数据发现）。
from backend.app.newapi.apikey.model import UserApiKey

# 2026-08-10：云端 Runtime 形态退役、`app/hermes` 整体删除，其中两张**非 Runtime** 的表随唯一
# 存活消费方（本模块）迁入。物理表名保持 `hermes_agent` / `hermes_agent_llm_token` 不变，零 migration。
from backend.app.newapi.model.hermes_agent import HermesAgent
from backend.app.newapi.model.hermes_agent_llm_token import HermesAgentLlmToken
from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping

__all__ = ['HermesAgent', 'HermesAgentLlmToken', 'LlmNewapiUserMapping', 'UserApiKey']
