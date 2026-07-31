"""service_registry —— 内部独立服务的「约定式服务目录」（单一真相 / 静态服务发现）。

**解决什么**：每加一个内部独立服务（finance / quant / ragflow / hermes / new-api …）就要在
`conf.py` + `.env` + 各 provider 三处散配 `URL/TOKEN/TIMEOUT`，命名还各不相同
（finance 用 `FINANCE_SERVICE_*`、quant 用 `QUANT_ENGINE_*`），服务一多就乱、易漏配、易配错文件。
本模块把每个内部服务**登记一次**（端口 / 健康路径 / 对应 settings 字段），成为唯一目录：

- :func:`service_endpoint` 按登记解析 ``base_url / token / timeout``，多源合并：
  **显式 env/settings 优先 → 结构化文件 ``services.toml`` 的 [service.<name>] 块 → dev 本机约定回落 →
  prod 留空**（由 provider 归一 ``service_unconfigured``，绝不在 prod 静默连本机掩盖漏配）。
- **令牌集中派生**：``derive_token`` 服务（finance/quant）若未显式配 token，则从单个 ``master_secret``
  派生 ``HMAC-SHA256(master_secret, 服务名)``（见 ``services_config``），与服务端逐字节一致，
  两边无需各配。``derive_token=False`` 的服务（ragflow/hermes/newapi）用第三方/外部/bespoke 真实
  鉴权，**绝不派生**。``pooled`` 与 ``derive_token`` 是两个独立维度：newapi 池化但不派生。
- 结构化部署值与主密钥都在 ``services.toml``（一服务一块，不再散落 .env），事实源见 doc25。
- :func:`iter_services` / 健康检查消费此目录，给统一管理页「一页看全部内部服务死活」。

**为何不引 Nacos/Consul**：内部服务是固定的一小撮单例、单机部署、cloud-brokered（云端是唯一消费方），
痛点是配置样板 / 可见性而非动态实例发现。约定式静态目录零新增中间件即拿到同等收益；将来真要多副本 /
动态扩缩再升级注册中心，本目录可平滑作为其数据源（事实源：见 ADR）。
"""

from __future__ import annotations

import os

from dataclasses import dataclass

from backend.common.services_config import (
    derive_service_token,
    master_secret,
    service_overrides,
)
from backend.core.conf import settings


@dataclass(frozen=True)
class ServiceSpec:
    """一个内部独立服务的登记项（声明期注册，无运行时动态部件）。"""

    name: str  # 规范服务名（连接池 key / 目录 key），如 'finance'
    title: str  # 展示名（管理页）
    default_port: int  # 本机约定端口（dev 零配置回落用；与一键启动脚本同源）
    url_attr: str  # settings 中的基址字段名
    token_attr: str | None  # settings 中的 bearer token 字段名（None=无鉴权）
    timeout_attr: str | None  # settings 中的超时(秒)字段名（None=用 default_timeout）
    health_path: str | None  # 健康检查路径（None=仅做连通性探测，任何 HTTP 响应即视为可达）
    pooled: bool  # True=经 service_http 进程级连接池 + 健康复用池（finance/quant/newapi）；
    # False=健康用临时 client、其自有 transport 维持原样（ragflow/hermes）
    derive_token: bool  # True=未显式配 token 时从 master_secret 派生（仅我方自研、两端受控：finance/quant）；
    # False=有第三方/外部/bespoke 真实鉴权，绝不派生（ragflow/hermes/newapi）
    default_timeout: float = 30.0


@dataclass(frozen=True)
class ServiceEndpoint:
    """:func:`service_endpoint` 的解析结果。"""

    name: str
    base_url: str  # 解析后的基址（已去尾斜杠；prod 未配时为 '' → provider 应归一 service_unconfigured）
    token: str  # bearer token（可能为 ''）
    timeout: float  # per-request 超时（秒）
    configured: bool  # 是否来自显式 settings/.env（False = dev 本机默认回落 或 完全未配）


# 内部服务目录（新增内部服务在此登记一行即可——这就是「注册」）。
# 约定端口：finance 8000 / quant 8001（单机 dev 不撞）；与一键启动脚本同源。
_REGISTRY: dict[str, ServiceSpec] = {
    spec.name: spec
    for spec in (
        ServiceSpec(
            name='finance',
            title='金融数据服务',
            default_port=8000,
            url_attr='FINANCE_SERVICE_URL',
            token_attr='FINANCE_SERVICE_TOKEN',
            timeout_attr='FINANCE_SERVICE_TIMEOUT',
            health_path='/v1/healthz',
            pooled=True,
            derive_token=True,
        ),
        ServiceSpec(
            name='quant',
            title='量化引擎服务',
            default_port=8001,
            url_attr='QUANT_ENGINE_URL',
            token_attr='QUANT_ENGINE_TOKEN',
            timeout_attr='QUANT_ENGINE_TIMEOUT',
            health_path='/v1/healthz',
            pooled=True,
            derive_token=True,
        ),
        # montage：OpenMontage fork 薄服务壳（统一视频引擎，cloud-brokered）。自研、两端受控 →
        # 派生令牌；池化（云端 broker 经 service_http 连接池调用）。约定端口 8002（dev 不撞）。
        # default_timeout=600：渲染段是同步可能耗时的合成调用（P0 实测 ≈3.5–4× 视频时长单核）。
        ServiceSpec(
            name='montage',
            title='视频引擎服务',
            default_port=8002,
            url_attr='MONTAGE_ENGINE_URL',
            token_attr='MONTAGE_ENGINE_TOKEN',
            timeout_attr='MONTAGE_ENGINE_TIMEOUT',
            health_path='/v1/healthz',
            pooled=True,
            derive_token=True,
            default_timeout=600.0,
        ),
        # lead-crawler：Scrapy 黄页/B2B 深爬服务（doc93 §3.1，cloud-brokered）。自研、两端受控 →
        # 派生令牌；池化（云端 broker 经 service_http 连接池调用）。约定端口 8003（dev 不撞）。
        # default_timeout=120：Scrapy 列表分页 → 详情页定向深爬是可能耗时的同步抓取（带 playwright 渲染）。
        ServiceSpec(
            name='lead-crawler',
            title='获客深爬服务',
            default_port=8003,
            url_attr='LEAD_CRAWLER_URL',
            token_attr='LEAD_CRAWLER_TOKEN',
            timeout_attr='LEAD_CRAWLER_TIMEOUT',
            health_path='/v1/healthz',
            pooled=True,
            derive_token=True,
            default_timeout=120.0,
        ),
        # hosting：无头 hasn-node 托管宿主代理（hasn-node-hosting agent，doc「云端节点托管」实施契约 §1/§4）。
        # 自研、两端受控 → 派生令牌；池化（云端 broker 经 service_http 连接池调用）。
        # ⚠️ 约定端口 8003 由实施契约 §1 指定，与上面的 lead-crawler 在 dev 本机回落上撞车——
        # 两者同机同时起时必须给其中一个显式配 URL（env/services.toml），否则会连错服务。
        # default_timeout=120：建容器要拉镜像 + 建网络/卷，是可能耗时的同步编排调用。
        ServiceSpec(
            name='hosting',
            title='无头节点托管宿主',
            default_port=8003,
            url_attr='HOSTING_AGENT_URL',
            token_attr='HOSTING_AGENT_TOKEN',
            timeout_attr='HOSTING_AGENT_TIMEOUT',
            health_path='/health',
            pooled=True,
            derive_token=True,
            default_timeout=120.0,
        ),
        # publish：Growth 公开表单解析站点权威绑定的内部 HTTP 接缝。当前可与主云端同进程部署，
        # 但 Growth 仍只经 provider 调用收敛接口；令牌显式配置，不允许匿名或派生回落。
        ServiceSpec(
            name='publish',
            title='网页发布服务',
            default_port=8000,
            url_attr='PUBLISH_INTERNAL_BASE_URL',
            token_attr='PUBLISH_INTERNAL_TOKEN',
            timeout_attr='PUBLISH_INTERNAL_TIMEOUT',
            health_path=None,
            pooled=True,
            derive_token=False,
            default_timeout=5.0,
        ),
        # 以下为外部/已部署服务：用各自第三方/bespoke 真实鉴权，绝不派生令牌（derive_token=False）。
        # ragflow/hermes 自有 transport（RSA / 双向 bespoke token）维持原样，目录登记仅供健康可见。
        ServiceSpec(
            name='ragflow',
            title='RAGFlow 知识库',
            default_port=18082,
            url_attr='RAGFLOW_PUBLIC_URL',
            token_attr=None,
            timeout_attr=None,
            health_path=None,
            pooled=False,
            derive_token=False,
        ),
        # hermes：自研 runtime（固定服务级 Bearer，双向另有 RUNTIME_INTERNAL_TOKEN）。控制面调用
        # 经 service_endpoint 取连接三元组；health_path='/health'（server.py 在 auth 之前响应 200，无鉴权）。
        # default_timeout=10 保持 hermes 历史默认（HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS）。
        ServiceSpec(
            name='hermes',
            title='Hermes Runtime',
            default_port=8765,
            url_attr='HUANXING_HERMES_RUNTIME_BASE_URL',
            token_attr='HUANXING_HERMES_RUNTIME_API_TOKEN',
            timeout_attr='HUANXING_HERMES_RUNTIME_TIMEOUT_SECONDS',
            health_path='/health',
            pooled=False,
            derive_token=False,
            default_timeout=10.0,
        ),
        # newapi：池化但不派生——`NewApiAdminClient` 经进程级连接池复用连接，但 token 是外部
        # new-api 系统的真实 admin 密钥，绝不能落入 master 派生分支（故 derive_token=False）。
        # health_path='/status'（base 含 /api → 命中 /api/status，无鉴权）。
        ServiceSpec(
            name='newapi',
            title='New-API 网关',
            default_port=3180,
            url_attr='NEWAPI_ADMIN_BASE_URL',
            token_attr='NEWAPI_ADMIN_ACCESS_TOKEN',
            timeout_attr='NEWAPI_HTTP_TIMEOUT_SECONDS',
            health_path='/status',
            pooled=True,
            derive_token=False,
        ),
    )
}


def iter_services() -> list[ServiceSpec]:
    """目录中所有内部服务登记（管理页 / 健康检查遍历用）。"""
    return list(_REGISTRY.values())


def get_service_spec(name: str) -> ServiceSpec:
    """取服务登记项；未登记即抛 KeyError（提示先在 _REGISTRY 注册）。"""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f'未登记的内部服务: {name!r}（请先在 backend/common/service_registry.py 注册）') from exc


def _is_dev() -> bool:
    return getattr(settings, 'ENVIRONMENT', 'prod') == 'dev'


def _resolve_base_url(spec: ServiceSpec, overrides: dict) -> tuple[str, bool]:
    """base_url：显式 env/settings 优先 → services.toml → dev 本机约定回落 → prod 留空。"""
    configured_url = (os.environ.get(spec.url_attr) or getattr(settings, spec.url_attr, '') or '').strip()
    if configured_url:
        return configured_url, True
    toml_url = str(overrides.get('url') or '').strip()
    if toml_url:
        return toml_url, True
    if _is_dev() and spec.default_port:
        return f'http://127.0.0.1:{spec.default_port}', False
    return '', False


def _resolve_token(spec: ServiceSpec, overrides: dict, name: str) -> str:
    """token：显式 env/settings → services.toml 显式 → 主密钥派生（仅 ``derive_token`` 服务）→ ''。

    派生判据是 ``spec.derive_token`` 而非 ``spec.pooled``：newapi 池化（pooled=True）但用外部真实
    admin 密钥（derive_token=False），绝不能误派生覆盖；ragflow/hermes 两者皆 False。
    """
    token = ''
    if spec.token_attr:
        token = os.environ.get(spec.token_attr) or getattr(settings, spec.token_attr, '') or ''
    if not token:
        token = str(overrides.get('token') or '')
    if not token and spec.derive_token:
        token = derive_service_token(master_secret(), name)
    return token


def _resolve_timeout(spec: ServiceSpec, overrides: dict) -> float:
    """timeout：显式 env/settings → services.toml → default。"""
    raw_timeout: object = None
    if spec.timeout_attr:
        raw_timeout = os.environ.get(spec.timeout_attr) or getattr(settings, spec.timeout_attr, None)
    if not raw_timeout:
        raw_timeout = overrides.get('timeout')
    if not raw_timeout:
        return spec.default_timeout
    try:
        return float(str(raw_timeout))
    except (TypeError, ValueError):
        return spec.default_timeout


def service_endpoint(name: str) -> ServiceEndpoint:
    """解析服务连接参数：显式配置优先 → services.toml → dev 本机默认回落 → prod 留空。

    :param name: 已登记的服务名（如 'finance' / 'quant'）。
    :return: :class:`ServiceEndpoint`；``base_url`` 为 '' 表示 prod 未配，调用方应归一 service_unconfigured。
    """
    spec = get_service_spec(name)
    overrides = service_overrides(name)
    base_url, configured = _resolve_base_url(spec, overrides)
    return ServiceEndpoint(
        name=name,
        base_url=base_url.rstrip('/'),
        token=_resolve_token(spec, overrides, name),
        timeout=_resolve_timeout(spec, overrides),
        configured=configured,
    )
