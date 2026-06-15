"""new-api 集成模块数据模型。"""

from backend.app.newapi.model.llm_newapi_user_mapping import LlmNewapiUserMapping

# 自建 API Key 模型物理上位于 apikey 子包（与其 crud/service/schema 同域）；在此显式导入，
# 确保 import app.newapi.model 时其 mapper 也随之注册进 MappedBase.metadata（建表/元数据发现）。
from backend.app.newapi.apikey.model import UserApiKey

__all__ = ['LlmNewapiUserMapping', 'UserApiKey']
