"""knowledge 实例配置解析（复用 hasn_app_instance，单平台租户，D2）。

设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §4.1：
- `app_id='knowledge', scope='public', status='active'` 单行；
- endpoint = RAGFlow 内网地址；credential_ref = 平台 service key 密文（key_encryption）；
- config = {default_embd_id, default_llm_id}；transport_default = gateway_internal。

凭据只活在云端：service key 在此解密后直接注入 client，永不出参、不进日志。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model.hasn_app_instance import HasnAppInstance
from backend.app.hasn_knowledge.service.ragflow_client import KnowledgeProviderError, RAGFlowClient
from backend.common.security.encryption import key_encryption

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class KnowledgeInstanceConfig:
    endpoint: str
    default_embd_id: str
    default_llm_id: str | None


async def resolve_knowledge_instance(db: AsyncSession) -> tuple[RAGFlowClient, KnowledgeInstanceConfig]:
    """解析 active public knowledge 实例 → 数据面 client + 配置。

    无 active 实例 / 配置不全 → knowledge_instance_not_configured（运营未配置 RAGFlow，如实报）。
    """
    row = (
        await db.execute(
            sa.select(HasnAppInstance)
            .where(
                HasnAppInstance.app_id == 'knowledge',
                HasnAppInstance.scope == 'public',
                HasnAppInstance.status == 'active',
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or not row.endpoint or not row.credential_ref:
        raise KnowledgeProviderError('knowledge_instance_not_configured', '知识库处理后端未配置（无 active 实例）')
    config = row.config or {}
    embd_id = config.get('default_embd_id')
    if not embd_id:
        raise KnowledgeProviderError('knowledge_instance_not_configured', '知识库实例缺少 default_embd_id 配置')
    try:
        api_key = key_encryption.decrypt(row.credential_ref)
    except Exception as exc:
        raise KnowledgeProviderError('knowledge_instance_not_configured', 'service key 解密失败（检查密钥配置）') from exc
    client = RAGFlowClient(row.endpoint, api_key)
    return client, KnowledgeInstanceConfig(
        endpoint=row.endpoint,
        default_embd_id=str(embd_id),
        default_llm_id=config.get('default_llm_id'),
    )
