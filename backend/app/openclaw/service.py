"""Openclaw Gateway 服务层"""

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.openclaw.model import GatewayConfig
from backend.app.openclaw.schema import (
    CloudUserConfig,
    ConfigSyncResponse,
    GatewayConfigCreate,
    GatewayTokenResponse,
)


# ==================== Token 生成 ====================

def generate_gateway_token() -> str:
    """生成 Gateway 认证 token
    
    格式: gw_<32字节随机字符串>
    """
    random_bytes = secrets.token_urlsafe(32)
    return f"gw_{random_bytes}"


def hash_gateway_token(token: str) -> str:
    """计算 Gateway token 的 SHA-256 哈希"""
    return hashlib.sha256(token.encode()).hexdigest()


# ==================== Gateway Config 服务 ====================

async def create_gateway_config(
    db: AsyncSession,
    user_id: int,
    data: GatewayConfigCreate,
    auto_commit: bool = True,
) -> GatewayTokenResponse:
    """为用户创建 Gateway 配置
    
    如果用户已有配置，则返回现有配置。
    只在创建时返回完整的 token。
    
    Args:
        auto_commit: 是否自动提交事务，默认 True。
                     当调用方使用事务管理器时应设为 False。
    """
    # 检查是否已存在
    stmt = select(GatewayConfig).where(GatewayConfig.user_id == user_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        # 已存在，返回现有 token（不重新生成）
        return GatewayTokenResponse(
            gateway_token=existing.gateway_token,
            user_id=user_id,
            status=existing.status,
        )
    
    # 创建新配置
    token = generate_gateway_token()
    config = GatewayConfig(
        user_id=user_id,
        gateway_token=token,
        gateway_token_hash=hash_gateway_token(token),
        openclaw_config=data.openclaw_config,
        status="active",
    )
    db.add(config)
    
    if auto_commit:
        await db.commit()
        await db.refresh(config)
    else:
        await db.flush()
    
    return GatewayTokenResponse(
        gateway_token=token,
        user_id=user_id,
        status=config.status,
    )


async def get_gateway_config_by_user(
    db: AsyncSession,
    user_id: int,
) -> GatewayConfig | None:
    """根据用户 ID 获取 Gateway 配置"""
    stmt = select(GatewayConfig).where(GatewayConfig.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_gateway_config(
    db: AsyncSession,
    user_id: int,
    openclaw_config: dict | None = None,
    status: str | None = None,
) -> GatewayConfig | None:
    """更新用户的 Gateway 配置"""
    config = await get_gateway_config_by_user(db, user_id)
    if not config:
        return None
    
    if openclaw_config is not None:
        config.openclaw_config = openclaw_config
    if status is not None:
        config.status = status
    
    await db.commit()
    await db.refresh(config)
    return config


# ==================== Config Sync 服务 ====================

async def sync_gateway_configs(
    db: AsyncSession,
    since: datetime | None = None,
    limit: int = 100,
    cursor: str | None = None,
) -> ConfigSyncResponse:
    """获取 Gateway 配置用于同步
    
    支持增量同步 (since 参数) 和分页。
    """
    stmt = select(GatewayConfig).where(GatewayConfig.status == "active")
    
    # 增量同步
    if since:
        stmt = stmt.where(GatewayConfig.updated_at > since)
    
    # 分页
    if cursor:
        stmt = stmt.where(GatewayConfig.id > int(cursor))
    
    stmt = stmt.order_by(GatewayConfig.id).limit(limit + 1)
    
    result = await db.execute(stmt)
    configs = list(result.scalars().all())
    
    # 检查是否有更多数据
    has_more = len(configs) > limit
    if has_more:
        configs = configs[:limit]
    
    # 转换为响应格式
    users: list[CloudUserConfig] = []
    for config in configs:
        # TODO: 从 LLM 模块获取用户的 API key
        llm_api_key = None  # 需要关联查询
        
        users.append(CloudUserConfig(
            user_id=str(config.user_id),
            gateway_token=config.gateway_token,
            openclaw_config=config.openclaw_config or {},
            status=config.status,
            llm_api_key=llm_api_key,
            updated_at=config.updated_at.isoformat(),
        ))
    
    # 更新同步时间
    sync_timestamp = datetime.utcnow().isoformat()
    next_cursor = str(configs[-1].id) if configs and has_more else None
    
    return ConfigSyncResponse(
        users=users,
        sync_timestamp=sync_timestamp,
        has_more=has_more,
        next_cursor=next_cursor,
    )


# ==================== Token 验证 ====================

async def verify_gateway_token(
    db: AsyncSession,
    token: str,
) -> dict | None:
    """验证 Gateway Token 并返回用户信息
    
    用于 Gateway 实时验证用户 token。
    
    Returns:
        包含 user_id, status, openclaw_config, llm_api_key, llm_api_base_url 的字典，或 None
    """
    import logging
    log = logging.getLogger(__name__)
    
    from backend.app.newapi.apikey.service import api_key_service
    from backend.core.conf import settings
    
    stmt = select(GatewayConfig).where(GatewayConfig.gateway_token == token)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        return None
    
    # 检查状态
    if config.status != "active":
        return None
    
    # 获取用户的 LLM API Key
    llm_api_key = None
    try:
        log.info(f"Getting LLM API key for user_id={config.user_id}")
        llm_key = await api_key_service.get_or_create_default_key(db, config.user_id)
        log.info(f"Got llm_key: has _decrypted_key={hasattr(llm_key, '_decrypted_key')}, _decrypted_key value={'set' if (hasattr(llm_key, '_decrypted_key') and llm_key._decrypted_key) else 'empty'}")
        if hasattr(llm_key, '_decrypted_key') and llm_key._decrypted_key:
            llm_api_key = llm_key._decrypted_key
            # Ensure the key we just created (or retrieved) is committed
            await db.commit()
            log.info(f"LLM API key set successfully for user_id={config.user_id}")
        else:
            log.warning(f"LLM API key not decrypted for user_id={config.user_id}")
    except Exception as e:
        # 如果获取 API Key 失败，记录日志并继续返回其他信息
        log.error(f"Failed to get LLM API key for user_id={config.user_id}: {e}")
    
    return {
        "user_id": str(config.user_id),
        "status": config.status,
        "openclaw_config": config.openclaw_config or {},
        "llm_api_key": llm_api_key,
        "llm_api_base_url": settings.LLM_API_BASE_URL,
    }


# ==================== 服务认证 ====================

def verify_service_token(token: str, expected_token: str) -> bool:
    """验证服务间认证 token
    
    使用 secrets.compare_digest 防止时序攻击
    """
    return secrets.compare_digest(token, expected_token)
