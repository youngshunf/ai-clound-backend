import shutil

from functools import cache
from re import Pattern
from typing import Any, Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from backend.core.path_conf import ENV_EXAMPLE_FILE_PATH, ENV_FILE_PATH
from backend.plugin.settings_source import PluginSettingsSource


class Settings(BaseSettings):
    """全局配置"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding='utf-8',
        extra='allow',
        case_sensitive=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """自定义配置源优先级"""
        return env_settings, dotenv_settings, PluginSettingsSource(settings_cls)

    # .env 当前环境
    ENVIRONMENT: Literal['dev', 'prod']

    # FastAPI
    FASTAPI_API_V1_PATH: str = '/api/v1'
    FASTAPI_TITLE: str = 'fba'
    FASTAPI_DESCRIPTION: str = 'FastAPI Best Architecture'
    FASTAPI_DOCS_URL: str = '/docs'
    FASTAPI_REDOC_URL: str = '/redoc'
    FASTAPI_OPENAPI_URL: str | None = '/openapi'
    FASTAPI_STATIC_FILES: bool = True

    # .env 数据库
    DATABASE_TYPE: Literal['mysql', 'postgresql']
    DATABASE_HOST: str
    DATABASE_PORT: int
    DATABASE_USER: str
    DATABASE_PASSWORD: str

    # 数据库
    DATABASE_ECHO: bool | Literal['debug'] = False
    DATABASE_POOL_ECHO: bool | Literal['debug'] = False
    DATABASE_SCHEMA: str = 'huanxing'
    DATABASE_CHARSET: str = 'utf8mb4'
    DATABASE_PK_MODE: Literal['autoincrement', 'snowflake'] = 'autoincrement'

    # new-api 独立数据库（与主库同一 PostgreSQL 实例，不同 database）
    NEWAPI_DATABASE_SCHEMA: str = 'newapi'

    # 唤星积分 → new-api quota 换算比例（1 积分 = 1 美元 = 500000 quota）
    # NEWAPI_QUOTA_PER_DOLLAR 为新名（= new-api QuotaPerUnit，协议精度非业务费率）；
    # NEWAPI_CREDITS_TO_QUOTA_RATE 保留为旧别名直至 P6 清理，二者同值。
    NEWAPI_CREDITS_TO_QUOTA_RATE: int = 500_000
    NEWAPI_QUOTA_PER_DOLLAR: int = 500_000

    # new-api 管理 HTTP API（DB 直连 → HTTP 管理 API 迁移，2026-06-15）
    # admin/root access_token + New-Api-User: <NEWAPI_ADMIN_USER_ID> 走 admin 端点；
    # 详见 docs/AI网关/实施/2026-06-15-new-api解耦改API与删除自建LLM模块迁移方案.md §13。
    NEWAPI_ADMIN_BASE_URL: str = 'http://localhost:3180/api'
    NEWAPI_ADMIN_ACCESS_TOKEN: str = ''
    NEWAPI_ADMIN_USER_ID: int = 1
    NEWAPI_HTTP_TIMEOUT_SECONDS: int = 15

    # 新用户注册赠送积分
    NEWAPI_REGISTER_BONUS_CREDITS: int = 500

    # .env Redis
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_PASSWORD: str
    REDIS_DATABASE: int

    # Redis
    REDIS_TIMEOUT: int = 5

    # 缓存
    CACHE_LOCAL_ENABLED: bool = True
    CACHE_LOCAL_MAXSIZE: int = 100000
    CACHE_LOCAL_TTL: int = 60 * 60 * 2  # 2 小时
    CACHE_REDIS_TTL: int = 60 * 60 * 2  # 2 小时
    CACHE_CONFIG_REDIS_PREFIX: str = 'fba:cache:config'
    CACHE_DICT_REDIS_PREFIX: str = 'fba:cache:dict'
    CACHE_PUBSUB_CHANNEL: str = 'fba:cache:invalidate'
    CACHE_PUBSUB_RECONNECT_DELAY: int = 5  # 重连延迟（秒）
    CACHE_PUBSUB_MAX_RECONNECT_ATTEMPTS: int = 10  # 最大重连次数

    # .env Snowflake
    SNOWFLAKE_DATACENTER_ID: int | None = None
    SNOWFLAKE_WORKER_ID: int | None = None

    # Snowflake
    SNOWFLAKE_REDIS_PREFIX: str = 'fba:snowflake'
    SNOWFLAKE_HEARTBEAT_INTERVAL_SECONDS: int = 30
    SNOWFLAKE_NODE_TTL_SECONDS: int = 60

    # .env Token
    TOKEN_SECRET_KEY: str  # 密钥 secrets.token_urlsafe(32)

    # Agent Key（OpenClaw 插件认证）
    AGENT_SECRET_KEY: str = ''  # 生产环境在 .env 中设置，支持逗号分隔多 key
    HUANXING_SITE_URL: str = 'https://huanxing.dcfuture.cn'  # 前端站点域名，用于生成分享链接等

    # Hermes Runtime（仅后端持有；不得返回给浏览器）
    HUANXING_HERMES_RUNTIME_BASE_URL: str = ''
    HUANXING_HERMES_RUNTIME_API_TOKEN: str = ''
    HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS: float = 10.0
    HUANXING_HERMES_RUNTIME_ID: str = 'hermes-runtime-local'

    # 内部 service token（runtime ↔ backend 单向调用，仅 .env 配置，不暴露浏览器）
    # 用于 X-Internal-Token header 校验（§09 §5）
    RUNTIME_INTERNAL_TOKEN: str = ''
    HUANXING_HERMES_PLATFORM_LLM_BASE_URL: str = 'https://api.huanxing.ai/api/v1/llm/proxy/v1'
    HUANXING_HERMES_PLATFORM_LLM_API_KEY: str = ''
    HUANXING_HERMES_PLATFORM_LLM_MODEL: str = 'openai/gpt-5.5'
    HUANXING_HERMES_PLATFORM_LLM_PLAN_ID: str = 'pro_monthly'
    HASN_TASK_CENTER_SCHEDULER_ENABLED: bool = False

    # RAGFlow 公共实例配置
    RAGFLOW_PUBLIC_URL: str = ''  # RAGFlow 服务地址，如 http://127.0.0.1:18082

    # 网页发布（模块 18）：制品内容绝不在 API 主域渲染——/s/* 整面落独立分享域名（usercontent 模式）。
    # 形如 https://share.huanxing.ai；为空时回退请求 origin（仅 dev/同域，生产必须配独立域名）。
    WEB_PUBLISH_SHARE_ORIGIN: str = ''
    RAGFLOW_PUBLIC_RSA_PUBLIC_KEY: str = ''  # RAGFlow RSA 公钥（PEM 格式），用于加密注册密码
    RAGFLOW_DEFAULT_EMBD_ID: str = 'BAAI/bge-large-zh-v1.5'  # 默认 embedding 模型
    RAGFLOW_DEFAULT_LLM_ID: str = 'deepseek-chat'  # 默认 LLM 模型

    # Token
    TOKEN_ALGORITHM: str = 'HS256'
    TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24  # 1 天
    TOKEN_REFRESH_EXPIRE_SECONDS: int = 60 * 60 * 24 * 7  # 7 天
    HASN_REFRESH_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24 * 30  # 30 天（桌面端长期登录）
    TOKEN_REDIS_PREFIX: str = 'fba:token'
    TOKEN_EXTRA_INFO_REDIS_PREFIX: str = 'fba:token_extra_info'
    TOKEN_ONLINE_REDIS_PREFIX: str = 'fba:token_online'
    TOKEN_REFRESH_REDIS_PREFIX: str = 'fba:refresh_token'
    TOKEN_REQUEST_PATH_EXCLUDE: list[str] = [  # JWT / RBAC 路由白名单
        f'{FASTAPI_API_V1_PATH}/auth/login',
        f'{FASTAPI_API_V1_PATH}/auth/send-code',
        f'{FASTAPI_API_V1_PATH}/auth/phone-login',
        f'{FASTAPI_API_V1_PATH}/hasn/auth/phone/send_code',
        f'{FASTAPI_API_V1_PATH}/hasn/auth/phone/verify',
        f'{FASTAPI_API_V1_PATH}/hasn/auth/token/refresh',
        f'{FASTAPI_API_V1_PATH}/user_tier/my/subscription/tiers',
        f'{FASTAPI_API_V1_PATH}/user_tier/my/subscription/packages',
    ]
    TOKEN_REQUEST_PATH_EXCLUDE_PATTERN: list[Pattern[str]] = [  # JWT / RBAC 路由白名单（正则）
        rf'^{FASTAPI_API_V1_PATH}/monitors/(redis|server)$',
        rf'^{FASTAPI_API_V1_PATH}/marketplace/client/.*$',  # 桌面端市场公开 API
        rf'^{FASTAPI_API_V1_PATH}/marketplace/download/.*$',  # 市场下载 API
        rf'^{FASTAPI_API_V1_PATH}/client/version/.*$',  # 桌面端版本检测公开 API
        rf'^{FASTAPI_API_V1_PATH}/llm/proxy(/.*)?$',  # LLM Proxy API（使用 x-api-key 认证，不走 JWT）
        rf'^{FASTAPI_API_V1_PATH}/huanxing/open/.*$',  # 唤星公开 API（分享文档等）
        rf'^{FASTAPI_API_V1_PATH}/hasn/agent/.*$',  # HASN Agent API（使用 AgentKey 认证）
        rf'^{FASTAPI_API_V1_PATH}/mcp/.*$',  # MCP Streamable 接入面（Agent MCP Key / Agent JWT 由 handler 自鉴权，不走 Owner JWT 中间件）
        rf'^{FASTAPI_API_V1_PATH}/hasn/open/.*$',  # HASN 公开 API
        rf'^{FASTAPI_API_V1_PATH}/hasn/ws/.*$',  # HASN WebSocket
        rf'^{FASTAPI_API_V1_PATH}/huanxing/agent/.*$',  # 唤星 Agent API（使用 X-Agent-Key 认证，不走 JWT）
        rf'^{FASTAPI_API_V1_PATH}/huanxing/user/.*$',  # 唤星用户级 API（使用 Owner Key 认证，不走 JWT）
        rf'^{FASTAPI_API_V1_PATH}/user_tier/agent/.*$',  # 订阅积分 Agent API（使用 X-Agent-Key 认证，不走 JWT）
        rf'^{FASTAPI_API_V1_PATH}/growth/agent/.*$',  # 获客 Agent API（Agent JWT，handler 自鉴权，不走 Owner JWT 中间件）
        rf'^{FASTAPI_API_V1_PATH}/lead-automation/agent/.*$',  # 获客旧前缀 Agent API（薄转发过渡，M8 退役）
        rf'^{FASTAPI_API_V1_PATH}/publish/agent/.*$',  # 网页发布 Agent API（Agent JWT，handler 自鉴权）
        r'^/s/[^/]+(/.*)?$',  # 网页发布公开查看面 /s/{slug}（独立分享域名，无鉴权外壳；模块 18）
        # 注：Agent JWT（Bearer，token_type=agent）的整类放行已不再依赖路径白名单——
        # JwtAuthMiddleware.extract_token 通过 is_agent_token 按 token 类型分流放行，
        # 交由路由自身的 DependsAgentJwtAuth 验签（守卫：tests/test_agent_jwt_middleware_bypass.py）。
        # 上面 *_agent/* 模式保留的是 X-Agent-Key（无 Authorization 头）等非 Bearer 自鉴权面。
    ]

    # 用户安全
    USER_LOCK_REDIS_PREFIX: str = 'fba:user:lock'
    USER_LOCK_THRESHOLD: int = 5  # 用户密码错误锁定阈值，0 表示禁用锁定
    USER_LOCK_SECONDS: int = 60 * 5  # 5 分钟
    USER_PASSWORD_EXPIRY_DAYS: int = 365  # 用户密码有效期，0 表示永不过期
    USER_PASSWORD_REMINDER_DAYS: int = 7  # 用户密码到期提醒，0 表示不提醒
    USER_PASSWORD_HISTORY_CHECK_COUNT: int = 3
    USER_PASSWORD_MIN_LENGTH: int = 6
    USER_PASSWORD_MAX_LENGTH: int = 32
    USER_PASSWORD_REQUIRE_SPECIAL_CHAR: bool = False

    # 登录
    LOGIN_CAPTCHA_ENABLED: bool = True
    LOGIN_CAPTCHA_REDIS_PREFIX: str = 'fba:login:captcha'
    LOGIN_CAPTCHA_EXPIRE_SECONDS: int = 60 * 5  # 5 分钟
    LOGIN_FAILURE_PREFIX: str = 'fba:login:failure'

    # JWT
    JWT_USER_REDIS_PREFIX: str = 'fba:user'

    # RBAC
    RBAC_ROLE_MENU_MODE: bool = True
    RBAC_ROLE_MENU_EXCLUDE: list[str] = [
        'sys:monitor:redis',
        'sys:monitor:server',
    ]

    # Cookie
    COOKIE_REFRESH_TOKEN_KEY: str = 'fba_refresh_token'
    COOKIE_REFRESH_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24 * 7  # 7 天

    # 数据权限
    DATA_PERMISSION_MODEL_EXCLUDE: list[str] = [  # 排除允许进行数据过滤的 SQLA 模型
        'DataScope',
        'DataRule',
        'sys_role_data_scope',
        'sys_data_scope_rule',
    ]
    DATA_PERMISSION_COLUMN_EXCLUDE: list[str] = [  # 排除允许进行数据过滤的 SQLA 模型列
        'id',
        'sort',
        'del_flag',
        'created_time',
        'updated_time',
    ]
    DATA_PERMISSION_MODEL_TEMPLATE_VARIABLES: list[dict[str, str]] = [  # 数据规则模型可用模板变量
        {'key': '__ALL__', 'comment': '所有模型'},
    ]
    DATA_PERMISSION_COLUMN_TEMPLATE_VARIABLES: list[dict[str, str]] = [  # 数据规则字段可用模板变量
        {'key': '__dept_id__', 'comment': '部门 ID'},
        {'key': '__created_by__', 'comment': '创建者'},
    ]
    DATA_PERMISSION_TEMPLATE_VARIABLES: list[dict[str, str]] = [  # 数据规则值可用模板变量
        {'key': '${user_id}', 'comment': '当前登录用户 ID'},
        {'key': '${dept_id}', 'comment': '当前登录用户部门 ID'},
        {'key': '${now}', 'comment': '当前时间'},
    ]

    # Socket.IO
    WS_NO_AUTH_MARKER: str = 'internal'

    # CORS (allow_credentials=True 时不能用 '*'，必须列出具体域名)
    CORS_ALLOWED_ORIGINS: list[str] = [  # 末尾不带斜杠
        'http://127.0.0.1:6310',
        'http://localhost:5173',
        'http://localhost:6310',
        'http://localhost:8020',
        'http://192.168.1.92:8020',
        'http://api.ai.dcfuture.cn',
    ]
    CORS_EXPOSE_HEADERS: list[str] = [
        'X-Request-ID',
    ]

    # 中间件配置
    MIDDLEWARE_CORS: bool = True

    # 请求限制配置
    REQUEST_LIMITER_REDIS_PREFIX: str = 'fba:limiter'

    # 时间配置
    DATETIME_TIMEZONE: str = 'Asia/Shanghai'
    DATETIME_FORMAT: str = '%Y-%m-%d %H:%M:%S'

    # 文件上传
    UPLOAD_READ_SIZE: int = 1024
    UPLOAD_IMAGE_EXT_INCLUDE: list[str] = ['jpg', 'jpeg', 'png', 'gif', 'webp']
    UPLOAD_IMAGE_SIZE_MAX: int = 5 * 1024 * 1024  # 5 MB
    UPLOAD_VIDEO_EXT_INCLUDE: list[str] = ['mp4', 'mov', 'avi', 'flv']
    UPLOAD_VIDEO_SIZE_MAX: int = 20 * 1024 * 1024  # 20 MB

    # 演示模式配置
    DEMO_MODE: bool = False
    DEMO_MODE_EXCLUDE: set[tuple[str, str]] = {
        ('POST', f'{FASTAPI_API_V1_PATH}/auth/login'),
        ('POST', f'{FASTAPI_API_V1_PATH}/auth/logout'),
        ('GET', f'{FASTAPI_API_V1_PATH}/auth/captcha'),
        ('POST', f'{FASTAPI_API_V1_PATH}/auth/refresh'),
    }

    # IP 定位配置
    IP_LOCATION_PARSE: Literal['online', 'offline', 'false'] = 'offline'
    IP_LOCATION_REDIS_PREFIX: str = 'fba:ip:location'
    IP_LOCATION_EXPIRE_SECONDS: int = 60 * 60 * 24  # 1 天

    # Trace ID
    TRACE_ID_REQUEST_HEADER_KEY: str = 'X-Request-ID'
    TRACE_ID_LOG_LENGTH: int = 32  # UUID 长度，必须小于等于 32
    TRACE_ID_LOG_DEFAULT_VALUE: str = '-'

    # 日志
    LOG_FORMAT: str = (
        '<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</> | <lvl>{level: <8}</> | <cyan>{request_id}</> | <lvl>{message}</>'
    )

    # 日志（控制台）
    LOG_STD_LEVEL: str = 'INFO'

    # 日志（文件）
    LOG_FILE_ACCESS_LEVEL: str = 'INFO'
    LOG_FILE_ERROR_LEVEL: str = 'ERROR'
    LOG_ACCESS_FILENAME: str = 'fba_access.log'
    LOG_ERROR_FILENAME: str = 'fba_error.log'

    # 操作日志
    OPERA_LOG_PATH_EXCLUDE: list[str] = [
        '/favicon.ico',
        '/docs',
        '/redoc',
        '/openapi',
        f'{FASTAPI_API_V1_PATH}/auth/login/swagger',
        f'{FASTAPI_API_V1_PATH}/oauth2/github/callback',
        f'{FASTAPI_API_V1_PATH}/oauth2/google/callback',
    ]
    OPERA_LOG_REDACT_KEYS: list[str] = [
        'password',
        'old_password',
        'new_password',
        'confirm_password',
    ]
    OPERA_LOG_QUEUE_MAXSIZE: int = 100000
    OPERA_LOG_QUEUE_BATCH_CONSUME_SIZE: int = 100
    OPERA_LOG_QUEUE_TIMEOUT: int = 60  # 1 分钟

    # Plugin 配置
    PLUGIN_PIP_CHINA: bool = True
    PLUGIN_PIP_INDEX_URL: str = 'https://mirrors.aliyun.com/pypi/simple/'
    PLUGIN_PIP_MAX_RETRY: int = 3
    PLUGIN_REDIS_PREFIX: str = 'fba:plugin'

    # I18n 配置
    I18N_DEFAULT_LANGUAGE: str = 'zh-CN'

    # Grafana
    GRAFANA_METRICS_ENABLE: bool = False
    GRAFANA_OTLP_GRPC_ENDPOINT: str = 'fba_alloy:4317'

    ##################################################
    # [ LLM ] 网关配置
    ##################################################
    # .env LLM 网关加密密钥 (Fernet, 可通过 Fernet.generate_key() 生成)
    LLM_ENCRYPTION_KEY: str = ''
    # LLM API 网关 URL
    LLM_API_BASE_URL: str | None = None
    # 默认 LLM 模型 — 透传给 hasn-node daemon 的 phone/verify 响应
    # `llm_model` 字段，由 daemon 写入每个 hermes profile 的
    # `config.yaml::model.default`。Vendor 可通过 .env 覆盖
    # （例如 `LLM_DEFAULT_MODEL='qwen-max'`）。后续若要按用户级别
    # 区分模型，可在 user 表加 `llm_model` 列让该值优先覆盖。
    LLM_DEFAULT_MODEL: str = 'gpt-5.5'
    # LiteLLM 调试模式（生产环境建议关闭）
    LITELLM_DEBUG: bool = False

    # 智能上下文压缩
    LLM_COMPRESS_ENABLED: bool = True
    LLM_COMPRESS_THRESHOLD_RATIO: float = 0.75
    LLM_COMPRESS_MESSAGE_THRESHOLD: int = 100
    LLM_COMPRESS_KEEP_MESSAGES: int = 6

    ##################################################
    # [ Marketplace ] 技能市场配置
    ##################################################
    # GitHub 仓库配置
    HUANXING_HUB_REPO_URL: str = 'https://github.com/youngshunf/huanxing-hub.git'
    HUANXING_HUB_LOCAL_PATH: str = '/tmp/huanxing-hub'

    # GitHub Webhook 配置
    GITHUB_WEBHOOK_SECRET: str = ''  # 生产环境在 .env 中设置

    # ClawHub 同步配置
    CLAWHUB_API_URL: str = 'https://clawhub.ai/api/v1'
    CLAWHUB_API_KEY: str = ''  # 可选，用于认证
    # ClawHub 定时同步每次抓取的技能数量上限：
    #   本地/测试默认 100；生产环境在 .env 设为 0 表示全量同步
    MARKETPLACE_CLAWHUB_SYNC_LIMIT: int = 100
    # ClawHub 下载量阈值：只同步 stats.downloads 严格大于该值的技能。
    #   0 = 不设阈值（全收）；生产设为 100 即"下载量超过 100 才同步"。
    MARKETPLACE_CLAWHUB_MIN_DOWNLOADS: int = 0
    # ClawHub 技能解压目录累计占用硬上限(GB)：达到即暂停后续下载（安全兜底）。
    MARKETPLACE_CLAWHUB_MAX_DISK_GB: float = 50.0

    # 市场缓存配置
    MARKETPLACE_CACHE_DIR: str = '/tmp/marketplace-cache'
    LLM_COMPRESS_SUMMARY_MODEL: str = 'claude-sonnet-4-5-20250929'
    LLM_COMPRESS_CACHE_TTL: int = 86400

    ##################################################
    # [ App ] task
    ##################################################
    # .env Redis
    CELERY_BROKER_REDIS_DATABASE: int

    # .env RabbitMQ
    # docker run -d --hostname fba-mq --name fba-mq  -p 5672:5672 -p 15672:15672 rabbitmq:latest
    CELERY_RABBITMQ_HOST: str
    CELERY_RABBITMQ_PORT: int
    CELERY_RABBITMQ_USERNAME: str
    CELERY_RABBITMQ_PASSWORD: str

    # 基础配置
    CELERY_BROKER: Literal['rabbitmq', 'redis'] = 'redis'
    CELERY_RABBITMQ_VHOST: str = ''
    CELERY_REDIS_PREFIX: str = 'fba:celery'
    CELERY_TASK_MAX_RETRIES: int = 5

    ##################################################
    # [ Plugin ] code_generator
    ##################################################
    CODE_GENERATOR_DOWNLOAD_ZIP_FILENAME: str

    ##################################################
    # [ Plugin ] oauth2
    ##################################################
    # .env
    OAUTH2_GITHUB_CLIENT_ID: str
    OAUTH2_GITHUB_CLIENT_SECRET: str
    OAUTH2_GOOGLE_CLIENT_ID: str
    OAUTH2_GOOGLE_CLIENT_SECRET: str

    # 基础配置（in plugin.toml）
    OAUTH2_STATE_REDIS_PREFIX: str
    OAUTH2_STATE_EXPIRE_SECONDS: int
    OAUTH2_GITHUB_REDIRECT_URI: str
    OAUTH2_GOOGLE_REDIRECT_URI: str
    OAUTH2_FRONTEND_LOGIN_REDIRECT_URI: str
    OAUTH2_FRONTEND_BINDING_REDIRECT_URI: str

    ##################################################
    # [ Plugin ] email
    ##################################################
    # .env
    EMAIL_USERNAME: str
    EMAIL_PASSWORD: str

    # 基础配置（in plugin.toml）
    EMAIL_HOST: str
    EMAIL_PORT: int
    EMAIL_SSL: bool
    EMAIL_CAPTCHA_REDIS_PREFIX: str
    EMAIL_CAPTCHA_EXPIRE_SECONDS: int

    ##################################################
    # [ Plugin ] sms
    ##################################################
    # .env SMS (Aliyun)
    SMS_ALIYUN_ACCESS_KEY_ID: str | None = None
    SMS_ALIYUN_ACCESS_KEY_SECRET: str | None = None
    SMS_ALIYUN_SIGN_NAME: str | None = None
    SMS_ALIYUN_TEMPLATE_CODE: str | None = None

    ##################################################
    # [ Agent ] Website Deployment
    ##################################################
    WEBSITE_DEPLOY_DIR: str | None = '/var/www/html/agents_sites'
    WEBSITE_BASE_URL: str | None = 'https://huanxing.dcfuture.cn/agents_sites'

    ##################################################
    # [ Mobile M1 ] Umeng U-Push (B5)
    ##################################################
    # 友盟 U-Push 服务端推送; UMENG_APP_MASTER_SECRET 仅后端持有 (D4).
    # 真实值走 Vault: secret/huanxing/backend/umeng; .env.example 仅占位.
    UMENG_APP_KEY: str = ''
    UMENG_APP_MASTER_SECRET: str = ''
    UMENG_PUSH_API_URL: str = 'https://msg.umeng.com/api/send'
    UMENG_PUSH_TIMEOUT_SECONDS: float = 5.0
    UMENG_PUSH_MAX_RETRIES: int = 3
    UMENG_PUSH_BACKOFF_BASE_SECONDS: float = 0.5
    UMENG_PUSH_PRODUCTION_MODE: bool = False

    ##################################################
    # [ Mobile M1 ] push_tokens PII static encryption (B10)
    ##################################################
    # push_tokens.token 静态加密主密钥 (Fernet base64 key; 真实值走 Vault).
    # 空值 → 进程内临时随机密钥 (仅 dev). 生产环境 ENVIRONMENT='prod' 必须注入.
    PUSH_TOKEN_ENCRYPTION_KEY: str = ''

    @model_validator(mode='before')
    @classmethod
    def check_env(cls, values: Any) -> Any:
        """检查环境变量"""
        if values.get('ENVIRONMENT') == 'prod':
            # FastAPI
            values['FASTAPI_OPENAPI_URL'] = None
            values['FASTAPI_STATIC_FILES'] = False

            # task —— broker 由 .env 的 CELERY_BROKER 决定（默认 redis）；
            # 生产若要用 RabbitMQ，在 .env 显式设置 CELERY_BROKER=rabbitmq（不再硬编码）

            # Grafana
            values['GRAFANA_METRICS_ENABLE'] = True

        return values


@cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    if not ENV_FILE_PATH.exists():
        shutil.copy(ENV_EXAMPLE_FILE_PATH, ENV_FILE_PATH)
    return Settings()


# 创建全局配置实例
settings = get_settings()
