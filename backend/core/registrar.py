import os

from asyncio import create_task
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import socketio

from fastapi import Depends, FastAPI
from fastapi_pagination import add_pagination
from prometheus_client import make_asgi_app
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend import __version__
from backend.common.cache.pubsub import cache_pubsub_manager
from backend.common.exception.exception_handler import register_exception
from backend.common.log import log, set_custom_logfile, setup_logging
from backend.common.observability.otel import init_otel
from backend.common.response.response_code import StandardResponseCode
from backend.core.conf import settings
from backend.core.path_conf import STATIC_DIR, UPLOAD_DIR
from backend.database.db import create_tables
from backend.database.redis import redis_client
from backend.middleware.access_middleware import AccessMiddleware
from backend.middleware.app_context_middleware import AppContextMiddleware
from backend.middleware.i18n_middleware import I18nMiddleware
from backend.middleware.jwt_auth_middleware import JwtAuthMiddleware
from backend.middleware.opera_log_middleware import OperaLogMiddleware
from backend.middleware.state_middleware import StateMiddleware
from backend.plugin.core import build_final_router
from backend.utils.demo_mode import demo_site
from backend.utils.openapi import ensure_unique_route_names, simplify_operation_ids
from backend.utils.serializers import MsgSpecJSONResponse
from backend.utils.snowflake import snowflake
from backend.utils.trace_id import OtelTraceIdPlugin


@asynccontextmanager
async def register_init(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    启动初始化

    :param app: FastAPI 应用实例
    :return:
    """
    # 创建数据库表
    await create_tables()

    # 应用目录播种（C2，设计 §6.1）：hasn_app_catalog 是工作台展示 DB 权威，
    # 启动期从内置注册表幂等播种缺失行（已存在行不被覆盖，运营改动保留）。
    try:
        from backend.app.hasn.service.app_catalog_service import ensure_catalog_seeded
        from backend.database.db import async_db_session

        async with async_db_session.begin() as seed_db:
            seeded = await ensure_catalog_seeded(seed_db)
        if seeded:
            log.info(f'应用目录播种：新增 {seeded} 行内置应用')
    except Exception as exc:  # noqa: BLE001
        log.warning(f'应用目录播种失败（忽略，运行期 Admin 可补）: {exc!r}')

    # 初始化 redis
    await redis_client.init()

    # 初始化 snowflake 节点
    await snowflake.init()

    # 创建操作日志任务
    create_task(OperaLogMiddleware.consumer())

    # 启动缓存 Pub/Sub 监听器
    cache_pubsub_manager.start_listener()

    # 注册支付业务回调
    from backend.app.billing.service.pay_callbacks import register_callbacks
    register_callbacks()

    # 注册 AI-Native 应用购买支付回调（C5：购买成功 → 写应用权益）
    from backend.app.hasn.service.app_purchase_callback import register_app_purchase_callback
    register_app_purchase_callback()

    # v2.1 默认由本地/云端 Runtime Host 调度任务；旧中心 scheduler 仅显式打开时运行。
    if settings.HASN_TASK_CENTER_SCHEDULER_ENABLED:
        from backend.app.hasn.service.task_scheduler import task_scheduler
        await task_scheduler.start()

    # 启动 MCP StreamableHTTP session manager
    from backend.app.mcp.streamable import hasn_streamable_server
    session_manager = hasn_streamable_server.create_session_manager()
    async with session_manager.run():
        yield

    # 停止缓存 Pub/Sub 监听器
    await cache_pubsub_manager.stop_listener()

    if settings.HASN_TASK_CENTER_SCHEDULER_ENABLED:
        from backend.app.hasn.service.task_scheduler import task_scheduler
        await task_scheduler.stop()

    # 释放 snowflake 节点
    await snowflake.shutdown()

    # 关闭 redis 连接
    await redis_client.aclose()


def register_app() -> FastAPI:
    """注册 FastAPI 应用"""

    app = FastAPI(
        title=settings.FASTAPI_TITLE,
        version=__version__,
        description=settings.FASTAPI_DESCRIPTION,
        docs_url=settings.FASTAPI_DOCS_URL,
        redoc_url=settings.FASTAPI_REDOC_URL,
        openapi_url=settings.FASTAPI_OPENAPI_URL,
        default_response_class=MsgSpecJSONResponse,
        lifespan=register_init,
    )

    # 注册组件
    register_logger()
    register_socket_app(app)
    register_static_file(app)
    register_middleware(app)
    register_router(app)
    register_page(app)
    register_exception(app)

    if settings.GRAFANA_METRICS_ENABLE:
        register_metrics(app)

    return app


def register_logger() -> None:
    """注册日志"""
    setup_logging()
    set_custom_logfile()


def register_static_file(app: FastAPI) -> None:
    """
    注册静态资源服务

    :param app: FastAPI 应用实例
    :return:
    """
    # 上传静态资源
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
    app.mount('/static/upload', StaticFiles(directory=UPLOAD_DIR), name='upload')

    # 固有静态资源
    if settings.FASTAPI_STATIC_FILES:
        app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


def register_middleware(app: FastAPI) -> None:
    """
    注册中间件（执行顺序从下往上）

    :param app: FastAPI 应用实例
    :return:
    """
    # Opera log
    app.add_middleware(OperaLogMiddleware)

    # App context (解析 X-App-Code)
    app.add_middleware(AppContextMiddleware)

    # State
    app.add_middleware(StateMiddleware)

    # JWT auth
    app.add_middleware(
        AuthenticationMiddleware,
        backend=JwtAuthMiddleware(),
        on_error=JwtAuthMiddleware.auth_exception_handler,
    )

    # I18n
    app.add_middleware(I18nMiddleware)

    # Access log
    app.add_middleware(AccessMiddleware)

    # ContextVar
    plugins = [OtelTraceIdPlugin()] if settings.GRAFANA_METRICS_ENABLE else [RequestIdPlugin(validate=True)]
    app.add_middleware(
        ContextMiddleware,
        plugins=plugins,
        default_error_response=MsgSpecJSONResponse(
            content={'code': StandardResponseCode.HTTP_400, 'msg': 'BAD_REQUEST', 'data': None},
            status_code=StandardResponseCode.HTTP_400,
        ),
    )

    # CORS
    # 注意: allow_credentials=True 时不能用 allow_origins=['*']，必须指定具体域名
    # 这样浏览器才会在跨域请求中发送 Cookie（refresh_token 依赖 httpOnly Cookie）
    if settings.MIDDLEWARE_CORS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
            expose_headers=settings.CORS_EXPOSE_HEADERS,
        )


def register_router(app: FastAPI) -> None:
    """
    注册路由

    :param app: FastAPI 应用实例
    :return:
    """
    dependencies = [Depends(demo_site)] if settings.DEMO_MODE else None

    # API
    router = build_final_router()
    app.include_router(router, dependencies=dependencies)

    # MCP Server 路由
    from backend.app.mcp.routes import register_mcp_routes
    register_mcp_routes(app)

    # Extra
    ensure_unique_route_names(app)
    simplify_operation_ids(app)


def register_page(app: FastAPI) -> None:
    """
    注册分页查询功能

    :param app: FastAPI 应用实例
    :return:
    """
    add_pagination(app)


def register_socket_app(app: FastAPI) -> None:
    """
    注册 Socket.IO 应用

    :param app: FastAPI 应用实例
    :return:
    """
    from backend.common.socketio.server import sio

    socket_app = socketio.ASGIApp(
        socketio_server=sio,
        other_asgi_app=app,
        # 切勿删除此配置：https://github.com/pyropy/fastapi-socketio/issues/51
        socketio_path='/ws/socket.io',
    )
    app.mount('/ws', socket_app)


def register_metrics(app: FastAPI) -> None:
    """
    注册指标

    :param app: FastAPI 应用实例
    :return:
    """
    metrics_app = make_asgi_app()
    app.mount('/metrics', metrics_app)

    init_otel(app)
