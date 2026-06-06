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

import sqlalchemy as sa

from backend.app.hasn.model import HasnAppInstance
from backend.core.conf import settings
from backend.database.db import async_db_session

KNOWLEDGE_APP_ID = 'knowledge'


async def seed_public_ragflow():
    """从配置文件 Seed 公共知识库实例（hasn_app_instance, app_id='knowledge', scope='public'）。"""

    url = settings.RAGFLOW_PUBLIC_URL
    public_key = settings.RAGFLOW_PUBLIC_RSA_PUBLIC_KEY
    default_embd_id = settings.RAGFLOW_DEFAULT_EMBD_ID
    default_llm_id = settings.RAGFLOW_DEFAULT_LLM_ID

    if not url:
        print("⚠️  未配置 RAGFlow 公共实例（RAGFLOW_PUBLIC_URL 为空）")
        print("   请在 .env 中配置后重试")
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
