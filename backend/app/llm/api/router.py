"""LLM API 路由注册"""

from fastapi import APIRouter

# api_keys / newapi 用户映射已迁出至 app/newapi（D1/D5，2026-06-15），路由改由 app/newapi/api/router.py 注册。
from backend.app.llm.api.v1 import compress_stats, images, media_tasks, model_alias, model_groups, models, providers, proxy, rate_limits, usage, videos

from backend.core.conf import settings

v1 = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/llm')

# 模型管理
v1.include_router(models.router, prefix='/models', tags=['LLM 模型管理'])

# 模型别名映射
v1.include_router(model_alias.router, prefix='/model-alias', tags=['LLM 模型别名映射'])

# 供应商管理
v1.include_router(providers.router, prefix='/providers', tags=['LLM 供应商管理'])

# 模型组管理
v1.include_router(model_groups.router, prefix='/model-groups', tags=['LLM 模型组管理'])

# 速率限制配置
v1.include_router(rate_limits.router, prefix='/rate-limits', tags=['LLM 速率限制配置'])

# 代理 API
v1.include_router(proxy.router, prefix='/proxy', tags=['LLM 代理'])

# 用量统计
v1.include_router(usage.router, prefix='/usage', tags=['LLM 用量统计'])

# 压缩统计（管理后台）
v1.include_router(compress_stats.router, prefix='/compress-stats', tags=['LLM 压缩统计'])

# 媒体任务管理
v1.include_router(media_tasks.router, prefix='/media-tasks', tags=['LLM 媒体任务管理'])

# 图像生成 API
v1.include_router(images.router, prefix='/proxy/v1/images', tags=['媒体生成 - 图像'])

# 视频生成 API
v1.include_router(videos.router, prefix='/proxy/v1/videos', tags=['媒体生成 - 视频'])

# new-api 用户映射管理（管理端）+ 用量查询已迁出至 app/newapi/api/router.py（D5），此处不再挂载。


# --- 用户端（仅 JWT） ---
app = APIRouter(prefix=f'{settings.FASTAPI_API_V1_PATH}/llm/app')
# new-api 用量与额度（用户端）已迁出至 app/newapi/api/router.py（D5），此处不再挂载。
