"""统一云端 LLM 客户端。

唤星云端**所有** LLM（OpenAI 兼容 chat completion）请求收敛到这里，默认走配置的
new-api 网关（``settings.LLM_API_BASE_URL`` / ``settings.LLM_API_KEY``）。集中处理：
base_url/api_key/模型默认、stream + SSE 解析、瞬时错误重试与退避、模型 fallback、以及
``trust_env=False``（绕开 macOS dev 系统代理对 localhost new-api 的劫持，曾致空 body 503）。

请勿在业务代码里再各自 ``httpx.post`` 拼 ``/chat/completions``——一律 import 这里的客户端。
"""

from backend.common.llm.client import LLMChatClient, LLMError, llm_client

__all__ = ['LLMChatClient', 'LLMError', 'llm_client']
