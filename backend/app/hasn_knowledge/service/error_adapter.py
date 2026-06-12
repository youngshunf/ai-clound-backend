"""knowledge_* 错误码 → HTTP 异常适配（emitter=cloud，设计 §6）。

稳定错误码以 `msg` 前缀形式携带（`<code>: <人话>`），daemon/webui 按前缀识别：
- knowledge_instance_not_configured → 500（运营未配置 RAGFlow）
- knowledge_provider_unreachable / knowledge_provider_error → 502（处理后端故障，绝不当空结果）
- knowledge_provider_unauthorized → 502（service key 失效，运营级故障）
- knowledge_grant_denied → 403（维度② denied / 白名单交集空）
"""

from __future__ import annotations

from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError
from backend.common.exception import errors


def to_http_error(exc: KnowledgeProviderError) -> errors.BaseExceptionError:
    msg = f'{exc.code}: {exc.message}'
    if exc.code == 'knowledge_grant_denied':
        return errors.ForbiddenError(msg=msg)
    if exc.code == 'knowledge_instance_not_configured':
        return errors.ServerError(msg=msg)
    return errors.GatewayError(msg=msg)
