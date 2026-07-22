import shutil

from functools import cache
from re import Pattern, compile
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
    # 启动期是否用 metadata.create_all 自动建表（云端 IM 服务化 R1-11）。
    # dev/演练默认 True（免手动建表）；生产**恒关**——建表与变更一律走 migration，
    # 禁止启动期 create_all 误建旧表 / 与迁移漂移（生产由下方 ENVIRONMENT=='prod' 硬闸兜底，
    # 即便此值被误设 True 也不生效，见 database/db.py::create_tables）。
    DATABASE_AUTO_CREATE_TABLES: bool = True

    # 云端 IM 服务化 R1-13：三 DB 角色专属连接串覆盖（§3.2「同进程也必须分 session maker」）。
    # 留空（默认）→ 该角色回落主连接（dev/演练：三角色共享主 engine、行为不变、全量测试照跑）。
    # 生产在 R3 窗口先建 astra_im_service / astra_sync_service / astra_python_backend 三 role 并
    # 授权后，再把对应 DSN 填入这三项，使 IM 域写 / sync 域写 / 通用后端各自经受限 role 落库
    # （role 的库级 grant 才是硬边界，本接缝只负责把 session maker 分置、随时可指向 role）。
    # 形如 postgresql+asyncpg://astra_im_service:***@host:5432/huanxing。
    IM_SERVICE_DATABASE_URL: str = ''
    SYNC_SERVICE_DATABASE_URL: str = ''
    PYTHON_BACKEND_DATABASE_URL: str = ''

    # 唤星积分 → new-api quota 换算比例（1 积分 = 1 美元 = 500000 quota）。
    # NEWAPI_QUOTA_PER_DOLLAR = new-api QuotaPerUnit（协议精度，非业务费率）。
    # （NEWAPI_DATABASE_SCHEMA + 旧别名 NEWAPI_CREDITS_TO_QUOTA_RATE 已随第二数据库引擎删除，2026-06-15。）
    NEWAPI_QUOTA_PER_DOLLAR: int = 500_000

    # new-api 管理 HTTP API（DB 直连 → HTTP 管理 API 迁移，2026-06-15）
    # admin/root access_token + New-Api-User: <NEWAPI_ADMIN_USER_ID> 走 admin 端点；
    # 详见 docs/AI网关/实施/2026-06-15-new-api解耦改API与删除自建LLM模块迁移方案.md §13。
    NEWAPI_ADMIN_BASE_URL: str = 'http://localhost:3180/api'
    NEWAPI_ADMIN_ACCESS_TOKEN: str = ''
    NEWAPI_ADMIN_USER_ID: int = 1
    NEWAPI_HTTP_TIMEOUT_SECONDS: int = 15
    # new-api 用户默认分组：relay 渠道按「用户分组」匹配可用渠道（token 空组继承用户组）。
    # new-api admin CreateUser 不接受 group 字段 → 新建用户分组为空字符串 → relay 报
    # 「No available channel for model X under group  ()」（空组匹配不到任何渠道）。
    # 故建用户后须显式把分组设为此值；空字符串表示不强制（沿用 new-api 行为）。
    NEWAPI_DEFAULT_USER_GROUP: str = 'default'

    # 新用户注册赠送积分（口径统一为 $100=100 积分，与免费档 subscription_tier(free).monthly_credits 一致；
    # 最终对账以账本为准，此值仅作新建用户初始 new-api quota，避免「先 $500 后被对账打回 $100」的瞬时不一致）
    NEWAPI_REGISTER_BONUS_CREDITS: int = 100

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
    # G1 平台特权门 bootstrap 兜底（doc18 §4.1）：`agent_hasn_id:scope[,agent_hasn_id:scope…]`，
    # 与 hasn_platform_operator_grants 表行同构，读入合并进 granted 集；仅应急，常态走 Admin 授予表
    PLATFORM_OPERATOR_AGENTS: str = ''
    HUANXING_SITE_URL: str = 'https://astra.dcfuture.cn'  # 前端站点域名，用于生成分享链接等（2026-07-03 起 huanxing→astra）

    # Hermes Runtime（仅后端持有；不得返回给浏览器）
    HUANXING_HERMES_RUNTIME_BASE_URL: str = ''
    HUANXING_HERMES_RUNTIME_API_TOKEN: str = ''
    HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS: float = 10.0
    HUANXING_HERMES_RUNTIME_ID: str = 'hermes-runtime-local'

    # 内部 service token（runtime ↔ backend 单向调用，仅 .env 配置，不暴露浏览器）
    # 用于 X-Internal-Token header 校验（§09 §5）
    RUNTIME_INTERNAL_TOKEN: str = ''

    # 桌面端发布与自动更新（hasn_release，模块「桌面端发布与自动更新」）
    # CI 构建完成回调密钥（Bearer；GitHub Actions 出包后回调 /hasn_release/ci/callback 落库）
    RELEASE_CI_CALLBACK_SECRET: str = ''
    # 管理端「从 GitHub 自动构建」触发 workflow_dispatch 所需（三者齐才真触发，缺则仅排队记录）
    RELEASE_GITHUB_TOKEN: str = ''  # GitHub PAT（repo + actions:write）
    RELEASE_GITHUB_REPO: str = 'youngshunf/hasn-node'  # owner/repo
    RELEASE_GITHUB_WORKFLOW: str = 'release-desktop.yml'  # workflow 文件名或 id（对齐 .github/workflows/release-desktop.yml）
    # 通用语音模型签名目录发布密钥（SPCAT-4·Bearer）：离线发布方 package-speech-model.sh --publish
    # 携此密钥先调 /speech-catalog/packages 暂存包，再调 /speech-catalog/releases 原子切换目录。
    # 未配置则拒绝所有发布（生产必须显式配置，避免误开放写库）。
    SPEECH_CATALOG_PUBLISH_SECRET: str = ''
    HUANXING_HERMES_PLATFORM_LLM_BASE_URL: str = 'https://api.huanxing.ai/api/v1/llm/proxy/v1'
    HUANXING_HERMES_PLATFORM_LLM_API_KEY: str = ''
    HUANXING_HERMES_PLATFORM_LLM_MODEL: str = 'openai/gpt-5.5'
    HUANXING_HERMES_PLATFORM_LLM_PLAN_ID: str = 'pro_monthly'
    # 云端 hermes 共享技能目录根（doc11 §5.3.5 B3）：与 hermes sidecar 同机部署时配置为
    # {hermes_runtime_root}/_shared/skills；未配置（None）= 该机器无 hermes sidecar，
    # marketplace_shared_skills_reconcile 任务 no-op。
    HERMES_SHARED_SKILLS_ROOT: str | None = None
    HASN_TASK_CENTER_SCHEDULER_ENABLED: bool = False

    # RAGFlow 公共实例配置
    RAGFLOW_PUBLIC_URL: str = ''  # RAGFlow 服务地址，如 http://127.0.0.1:18082

    # 网页发布（模块 18）：制品内容绝不在 API 主域渲染——/s/* 整面落独立分享域名（usercontent 模式）。
    # 形如 https://share.huanxing.ai；为空时回退请求 origin（仅 dev/同域，生产必须配独立域名）。
    WEB_PUBLISH_SHARE_ORIGIN: str = ''
    RAGFLOW_PUBLIC_RSA_PUBLIC_KEY: str = ''  # RAGFlow RSA 公钥（PEM 格式），用于加密注册密码
    RAGFLOW_DEFAULT_EMBD_ID: str = 'BAAI/bge-large-zh-v1.5'  # 默认 embedding 模型
    RAGFLOW_DEFAULT_LLM_ID: str = 'deepseek-chat'  # 默认 LLM 模型

    # 金融数据服务（finance-data-service，独立部署，模块 24 doc）：唯一接触 akshare 的地方，
    # 主云端经 finance_provider（httpx）中转取数（agent 工具面 + owner read-API 共用）。
    FINANCE_SERVICE_URL: str = ''  # 数据服务地址，如 http://finance-svc.internal:8000（为空时 provider 归一 service_unconfigured）
    FINANCE_SERVICE_TOKEN: str = ''  # 内部 svc-token（Bearer，对齐数据服务 FIN_SVC_TOKEN）
    FINANCE_SERVICE_TIMEOUT: int = 30  # HTTP 超时（秒）

    # 量化交易引擎服务（quant-engine-service，独立部署，模块 14 doc23）：唯一接触 NautilusTrader 的地方，
    # 主云端经 quant_engine_provider（httpx）中转提交/轮询回测（agent 工具面 + owner read-API 共用）。
    QUANT_ENGINE_URL: str = ''  # 引擎服务地址，如 http://quant-svc.internal:8000（为空时 provider 抛 QuantEngineError / healthz 归一 service_unconfigured）
    QUANT_ENGINE_TOKEN: str = ''  # 内部 svc-token（Bearer，对齐引擎服务 QUANT_SVC_TOKEN；空则引擎仅允许本机回环，开发态）
    QUANT_ENGINE_TIMEOUT: int = 30  # HTTP 超时（秒）

    # 获客采集引擎（firecrawl，独立部署，模块 07 doc）：唯一接触 firecrawl 的地方，hasn_growth
    # 采集 provider 经 FirecrawlClient（httpx）搜索/抓取/抽取线索。为空时回落
    # DEFAULT_FIRECRAWL_BASE_URL；api_key 为空则不带 Authorization（自托管 USE_DB_AUTHENTICATION=false）。
    FIRECRAWL_BASE_URL: str = ''  # firecrawl 服务地址，如 http://firecrawl-svc.internal:3002
    FIRECRAWL_API_KEY: str = ''  # firecrawl API Key（自托管可空）

    # 获客深爬服务（lead-crawler-service，独立部署，doc93 §3.1·huanxing-apps B 类）：Scrapy 有独立
    # Twisted reactor 与 FastAPI async 冲突 → 独立内部服务。ScrapyProvider（yellow_pages/b2b）
    # 经此 cloud-brokered 中转 POST /v1/crawl 出详情页线索。为空时 provider 归一 service_unconfigured
    # （prod 未配诚实不出数·零 fake）。Bearer 令牌由 services.toml master_secret 派生（对齐 finance/quant）。
    LEAD_CRAWLER_URL: str = ''  # 深爬服务地址，如 http://lead-crawler.internal:8003（为空时 provider 归一 service_unconfigured）
    LEAD_CRAWLER_TOKEN: str = ''  # 内部 svc-token（Bearer，空则从 master_secret 派生）
    LEAD_CRAWLER_TIMEOUT: int = 120  # HTTP 超时（秒，深爬可能耗时）

    # hasn_growth 线索结构化提取 LLM（方案 A：firecrawl 只抓 markdown，结构化提取在后端调
    # new-api 网关完成；详见 docs/AI自动获客任务系统/08-...）。网关 base_url/key 复用上方
    # LLM_API_BASE_URL/LLM_API_KEY，这里只配提取模型名；网关任一为空则跳过 LLM 提取，
    # cleaner 退回正则兜底，不影响采集主链路。
    GROWTH_LLM_MODEL: str = 'agnes-2.0-flash'  # 结构化提取模型（new-api 模型名）

    # 获客线索付费（doc93 §4.2）：用户「请求线索」前置额度闸——免费额度内放行，超额走支付购买。
    # 线索是**独立支付商品**，按支付订单结算，**不走 new-api 积分**（doc93 line 165 铁律）。
    # 免费额度按月重置（lead_quota.period_key 变更即归零）；单价按条计（分），运营改环境变量不动代码。
    GROWTH_FREE_LEADS_PER_MONTH: int = 50  # 每用户每月免费可领取线索条数
    GROWTH_LEAD_UNIT_PRICE_FEN: int = 100  # 购买线索单价（分/条），默认 ¥1.00/条

    # 获客采集 — 地图 POI 源（doc93 §3.2 maps 混合架构）：地图走官方 Place API 直出 POI，
    # **跳过 firecrawl + LLM**（POI 本就结构化）。高德优先，回落百度；都为空则 maps 源诚实跳过
    # （不 fake，真实 key 由运营配置，真抓 E2E infra-gated）。配置即生效，不动代码。
    AMAP_API_KEY: str = ''  # 高德地图 Web 服务 API Key（env AMAP_API_KEY）
    BAIDU_MAP_AK: str = ''  # 百度地图 Web 服务 AK（env BAIDU_MAP_AK）

    # 获客采集 — 企业工商官方 API 源（doc93 §3.3，可选）：企查查/天眼查官方 API 直返结构化企业信息，
    # 作为硬爬的「可切换的更稳路径」。端点 + key 由运营按所用厂商配置（不硬编码厂商 URL，避免猜错），
    # 响应走防御式多键解析（兼容企查查/天眼查字段名）。base/key 任一为空 → enterprise 源诚实跳过
    # （零 fake，真实账号 infra-gated）。key 只进 .env / 密钥管理，**绝不入库 / 绝不提交**。
    ENTERPRISE_INFO_API_BASE: str = ''  # 工商 API 搜索端点（env ENTERPRISE_INFO_API_BASE）
    ENTERPRISE_INFO_API_KEY: str = ''  # 工商 API key/token（env ENTERPRISE_INFO_API_KEY）

    # 获客采集 — 住宅代理池（doc93 §4.1 反爬底座）：**平台成本·运营配置·不向用户计费**。
    # firecrawl 自托管侧配 PROXY_SERVER 环境变量、Scrapy 侧走代理中间件；此处仅作云端侧的
    # 代理出口配置接缝（传给 lead-crawler-service 的抓取请求），为空表示不启用代理。
    # 凭据仅服务端持有，**绝不入库 / 绝不提交**（真实账号 infra-gated）。
    GROWTH_PROXY_POOL_URL: str = ''  # 住宅代理出口 URL（如 socks5h://user:pass@host:port），env GROWTH_PROXY_POOL_URL

    # 统一视频引擎（studio / montage-engine-service）BYO 长尾媒体凭据平台兜底（doc22 §5 P7）：
    # 主人没自带 key 时，对该 provider 用平台公共 key（运营自费），为空则该 provider 跳过（诚实，零 fake）。
    # 网关族（image/tts/stt/video）不在此——它们经 new-api 用主人自己的 relay token（OWNER 配额计费），
    # 不需要平台 BYO key。下列仅长尾 provider（fal / suno / heygen …），与 media_credentials.py 路由表对齐。
    MONTAGE_FALLBACK_FAL_KEY: str = ''  # fal.ai / Kling 等图像/视频 provider 平台兜底 key（env FAL_KEY）
    MONTAGE_FALLBACK_SUNO_KEY: str = ''  # Suno 音乐 provider 平台兜底 key（env SUNO_API_KEY）
    MONTAGE_FALLBACK_HEYGEN_KEY: str = ''  # HeyGen 数字人 provider 平台兜底 key（env HEYGEN_API_KEY）

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
        compile(rf'^{FASTAPI_API_V1_PATH}/monitors/(redis|server)$'),
        compile(rf'^{FASTAPI_API_V1_PATH}/marketplace/client/.*$'),  # 桌面端市场公开 API
        compile(rf'^{FASTAPI_API_V1_PATH}/marketplace/download/.*$'),  # 市场下载 API
        compile(rf'^{FASTAPI_API_V1_PATH}/client/version/.*$'),  # 桌面端版本检测公开 API
        compile(rf'^{FASTAPI_API_V1_PATH}/llm/proxy(/.*)?$'),  # LLM Proxy API（使用 x-api-key 认证，不走 JWT）
        compile(rf'^{FASTAPI_API_V1_PATH}/huanxing/open/.*$'),  # 唤星公开 API（分享文档等）
        compile(rf'^{FASTAPI_API_V1_PATH}/hasn/agent/.*$'),  # HASN Agent API（使用 AgentKey 认证）
        compile(rf'^{FASTAPI_API_V1_PATH}/mcp/.*$'),  # MCP Streamable 接入面（Agent MCP Key / Agent JWT 由 handler 自鉴权，不走 Owner JWT 中间件）
        compile(rf'^{FASTAPI_API_V1_PATH}/hasn/open/.*$'),  # HASN 公开 API
        compile(rf'^{FASTAPI_API_V1_PATH}/release/(open|ci)/.*$'),  # 桌面端发布：下载页/updater 公开面 + CI 回调（自带 Bearer CI 密钥自鉴权，不走 Owner JWT 中间件）
        compile(rf'^{FASTAPI_API_V1_PATH}/hasn/ci/speech-catalog/.*$'),  # 通用语音签名目录 CI 发布面（自带 Bearer CI 密钥自鉴权，不走 Owner JWT 中间件）
        compile(rf'^{FASTAPI_API_V1_PATH}/hasn/ws/.*$'),  # HASN WebSocket
        compile(rf'^{FASTAPI_API_V1_PATH}/huanxing/agent/.*$'),  # 唤星 Agent API（使用 X-Agent-Key 认证，不走 JWT）
        compile(rf'^{FASTAPI_API_V1_PATH}/huanxing/user/.*$'),  # 唤星用户级 API（使用 Owner Key 认证，不走 JWT）
        compile(rf'^{FASTAPI_API_V1_PATH}/user_tier/agent/.*$'),  # 订阅积分 Agent API（使用 X-Agent-Key 认证，不走 JWT）
        compile(rf'^{FASTAPI_API_V1_PATH}/growth/agent/.*$'),  # 获客 Agent API（Agent JWT，handler 自鉴权，不走 Owner JWT 中间件）
        compile(rf'^{FASTAPI_API_V1_PATH}/lead-automation/agent/.*$'),  # 获客旧前缀 Agent API（薄转发过渡，M8 退役）
        compile(rf'^{FASTAPI_API_V1_PATH}/publish/agent/.*$'),  # 网页发布 Agent API（Agent JWT，handler 自鉴权）
        compile(r'^/s/[^/]+(/.*)?$'),  # 网页发布公开查看面 /s/{slug}（独立分享域名，无鉴权外壳；模块 18）
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

    # HASN IM 节点 WebSocket 协议层运行参数（R1-09 协议层纯化：把原 ws_node 硬编码常量抽为配置）。
    # HASN_WS_SEND_TIMEOUT_SECS：单条控制帧下发的有界刷新窗口——超时即按连接失败进入清理，
    #   本质是 backpressure 上界（慢/满的 transport 不会无限阻塞收发循环，原 ws_node 硬编码 10.0）。
    HASN_WS_SEND_TIMEOUT_SECS: float = 10.0
    # HASN_WS_MAX_INBOUND_FRAME_BYTES：单条入站帧的 UTF-8 字节上限（frame size 硬闸）。0=不限
    #   （默认，保持现网行为、正常路径零额外开销）；>0 时超限帧显式回 2005 错误帧并 continue（不断连）。
    HASN_WS_MAX_INBOUND_FRAME_BYTES: int = 0
    # HASN_WS_MIN_CLIENT_VERSION：WS 握手最低客户端版本闸（R2-10·§8.3-2「掉队客户端闸」）。
    #   空串（默认）= 闸关，不闸任何 daemon 版本（本地重构/测试阶段——对齐「本地测试通过最后才生产
    #   部署」）；非空 = 最低放行版本（点分，如 '1.4.0'），低于此的 daemon 握手即以 4003
    #   (UPGRADE_REQUIRED) 拒连，供 D3 出「需要升级」引导并停重连风暴。R3 窗口设为配套 daemon 版本、
    #   切换即生效。fail-closed：阈值非空时无可解析版本头的客户端一律判为过低拒连（§8.3-2）。
    HASN_WS_MIN_CLIENT_VERSION: str = ''

    # CORS (allow_credentials=True 时不能用 '*'，必须列出具体域名)
    CORS_ALLOWED_ORIGINS: list[str] = [  # 末尾不带斜杠
        'http://127.0.0.1:5173',
        'http://127.0.0.1:6310',
        'http://localhost:5173',
        'http://localhost:6310',
        'http://localhost:8020',
        'http://192.168.1.92:8020',
        'http://api.ai.dcfuture.cn',
        'https://astra.dcfuture.cn',  # 官网/分享查看器前端域名（website /s/{slug} fetch publish/open meta+换票；2026-07-03 起 huanxing→astra）
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
    # LLM API 网关 Key（OpenAI 兼容 Bearer；new-api 等）。获客线索结构化提取（hasn_growth 方案A）
    # 复用此对 base_url/key，仅模型名走 GROWTH_LLM_MODEL，不再单独配 GROWTH_LLM_BASE_URL/API_KEY。
    LLM_API_KEY: str = ''
    # 默认 LLM 模型 — 透传给 hasn-node daemon 的 phone/verify 响应
    # `llm_model` 字段，由 daemon 写入每个 hermes profile 的
    # `config.yaml::model.default`。Vendor 可通过 .env 覆盖
    # （例如 `LLM_DEFAULT_MODEL='qwen-max'`）。后续若要按用户级别
    # 区分模型，可在 user 表加 `llm_model` 列让该值优先覆盖。
    LLM_DEFAULT_MODEL: str = 'gpt-5.5'
    # 云端后端**内部** LLM 任务（owner 记忆合并 / 画像完整度判定 / 翻译 / 获客提取 等，
    # 统一经 backend.common.llm.llm_client）的默认 **failover 模型链**：逐模型自动切换，
    # 前一个失败/空回退下一个，整条穷尽才算失败。区别于上面的 `LLM_DEFAULT_MODEL`
    # （那个透传给 hermes 作 **agent 运行时**默认模型，不是后端自身任务）。
    # 可经 .env 覆盖（JSON 数组，如 `LLM_DEFAULT_MODELS='["m1","m2"]'`）。
    LLM_DEFAULT_MODELS: list[str] = ['deepseek-v4-flash', 'deepseek-v4-pro', 'qwen3.7-plus']
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
    WEBSITE_BASE_URL: str | None = 'https://astra.dcfuture.cn/agents_sites'

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
