# -*- coding: utf-8 -*-
"""
Seed 公共知识库（RAGFlow）实例到数据库

从 .env 配置文件读取公共 RAGFlow 实例配置，写入统一应用平台底座
hasn_app_instance(app_id='knowledge', scope='public')。
用于本地开发测试和生产环境部署。

实施 03 收编后：旧 hasn_ragflow_instance 已删除，本脚本改写通用实例表
（RAGFlow 私有字段 public_pem/embd/llm 下沉 config，加密统一 key_encryption）。
"""

import asyncio
import os

import sqlalchemy as sa

from backend.app.hasn.model import HasnAppInstance
from backend.common.service_registry import service_endpoint
from backend.common.services_config import service_overrides
from backend.core.conf import settings
from backend.database.db import async_db_session

KNOWLEDGE_APP_ID = 'knowledge'


def _bootstrap_value(env_attr: str, override_key: str, overrides: dict) -> str:
    """bootstrap 字段解析：显式 env → settings → services.toml [service.ragflow]（保留原值不裁剪，PEM 含换行）。"""
    value = os.environ.get(env_attr)
    if value is None:
        value = getattr(settings, env_attr, '') or ''
    if not value:
        value = str(overrides.get(override_key) or '')
    return value


async def seed_public_ragflow():
    """从统一服务目录 Seed 公共知识库实例（hasn_app_instance, app_id='knowledge', scope='public'）。

    bootstrap 配置经 service_registry/services_config 统一解析：base_url 走
    service_endpoint('ragflow')（env RAGFLOW_PUBLIC_URL → settings → [service.ragflow].url →
    dev 回落约定端口）；RSA 公钥 / 默认 embd/llm 走 [service.ragflow] 扩展字段（env RAGFLOW_* 仍优先）。
    **数据面 per-instance 加密凭据（credential_ref）不在此处，保持 DB 加密存、零改动。**
    """

    ep = service_endpoint('ragflow')
    overrides = service_overrides('ragflow')
    url = ep.base_url
    public_key = _bootstrap_value('RAGFLOW_PUBLIC_RSA_PUBLIC_KEY', 'rsa_public_key', overrides)
    default_embd_id = _bootstrap_value('RAGFLOW_DEFAULT_EMBD_ID', 'default_embd_id', overrides)
    default_llm_id = _bootstrap_value('RAGFLOW_DEFAULT_LLM_ID', 'default_llm_id', overrides)

    if not url:
        print("⚠️  未配置 RAGFlow 公共实例（prod 下 RAGFLOW_PUBLIC_URL / [service.ragflow].url 均为空）")
        print("   请在 .env 或 services.toml [service.ragflow] 中配置后重试（dev 会回落本机约定端口）")
        return None

    if not public_key:
        print("⚠️  未配置 RAGFlow RSA 公钥（RAGFLOW_PUBLIC_RSA_PUBLIC_KEY 为空）")
        print("   注意：RAGFlow 注册需要 RSA 公钥来加密密码")
        print("   请从 RAGFlow 管理后台获取公钥并配置到 .env")
        # 继续创建记录，但 provision 会失败直到配置公钥

    config: dict = {}
    if public_key:
        config['public_pem'] = public_key
    if default_embd_id:
        config['default_embd_id'] = default_embd_id
    if default_llm_id:
        config['default_llm_id'] = default_llm_id

    async with async_db_session() as db:
        # 每个 app_id 至多一条 public 实例（partial unique）→ 按 (app_id, scope=public) upsert
        existing = (
            await db.execute(
                sa.select(HasnAppInstance).where(
                    HasnAppInstance.app_id == KNOWLEDGE_APP_ID,
                    HasnAppInstance.scope == 'public',
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.endpoint = url
            existing.transport_default = 'daemon_direct'
            existing.config = config
            existing.status = 'active'
            await db.commit()
            await db.refresh(existing)
            print(f"✓ 公共知识库实例已存在并更新 (ID: {existing.id})")
            print(f"  Endpoint: {url}")
            print(f"  Status: {existing.status}")
            print(f"  Public Key: {'已配置' if config.get('public_pem') else '未配置'}")
            return existing.id

        # public 实例不需要 admin key（provision 走 RSA 公钥注册），credential_ref 留空
        instance = HasnAppInstance(
            app_id=KNOWLEDGE_APP_ID,
            scope='public',
            enterprise_id=None,
            endpoint=url,
            transport_default='daemon_direct',
            credential_ref='',
            status='active',
            config=config,
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)

        print(f"✓ 成功创建公共知识库实例 (ID: {instance.id})")
        print(f"  Endpoint: {url}")
        print("  Scope: public")
        print("  Status: active")
        print(f"  Default Embedding: {default_embd_id}")
        print(f"  Default LLM: {default_llm_id}")
        print(f"  Public Key: {'已配置' if config.get('public_pem') else '未配置（需要配置才能正常 provision）'}")

        return instance.id


if __name__ == '__main__':
    asyncio.run(seed_public_ragflow())
