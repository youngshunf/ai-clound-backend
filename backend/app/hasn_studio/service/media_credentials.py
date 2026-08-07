"""媒体凭据解析 + BYO 管理（doc22 §5 P7，cloud-brokered）。

引擎（OpenMontage/montage-engine-service）每次出片/调原子工具，需要访问外部媒体 provider
（图像/语音/音乐/数字人 …）的 API。**凭据绝不持久在引擎侧**：云端按主人身份每次解析出一张
``{ENV_NAME: value}`` 临时覆盖表，随调用下发；引擎瞬时套用（``os.environ`` 级），用完即弃。

两条路（按 provider 族决定，见 ``_FAMILY_ROUTING``）：

(a) **唤星媒体网关（new-api）—— 优先**：OpenAI 兼容族（image / tts / stt，含 new-api 支持的 video）
    走主人**自己**的 new-api relay token，于是主人配额 + 计费 + 失败转移自动生效。
    → ``{OPENAI_API_KEY: sk-<owner newapi token>, OPENAI_BASE_URL: <new-api relay base>}``。
    复用 ``llm_newapi_user_mapping`` 既有解密映射，**不**手搓铸 token；主人无映射 → 优雅跳过
    （诚实，零 fake，引擎那一族能力即不可用而非假装可用）。

(b) **BYO 长尾**（fal/Kling、Suno、HeyGen …）：主人自带 key 用 ``key_encryption`` 加密存
    ``hasn_app_credential``（``app_id='studio'``，**绝不**落任何 hasn_studio 表），调用时解密塞进对应
    ENV。主人没配 → 用 settings 平台兜底 key（运营自费）；都没有 → 省略该 ENV（引擎那条工具自然报缺凭据）。

**安全铁律**：明文只在调用瞬间存在；**绝不**记录/返回明文或密文；凭据按 owner 隔离（一条凭据绑其主人，
绝不跨主人）。``resolve_media_credentials`` 失败一律保守（取不到就省略该项，绝不连平台 key 误下发给别人）。
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model import HasnAppCredential
from backend.app.hasn_core import identity
from backend.app.newapi.crud import llm_newapi_user_mapping_dao
from backend.common.exception import errors
from backend.common.security.encryption import key_encryption
from backend.core.conf import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# BYO 凭据归属的 app_id（hasn_app_credential.app_id）；与任何 hasn_studio 表无关。
STUDIO_APP_ID = 'studio'

# 网关路（a）的环境覆盖键名（引擎里 OpenAI 兼容 provider 读这两个）。
ENV_OPENAI_API_KEY = 'OPENAI_API_KEY'
ENV_OPENAI_BASE_URL = 'OPENAI_BASE_URL'

# new-api relay token 明文前缀（new-api 中间件去 sk- 前缀后取 parts[0]，与 newapi service.get_api_key 一致）。
_NEWAPI_KEY_PREFIX = 'sk-'


@dataclass(frozen=True)
class ByoProvider:
    """一个 BYO 长尾 provider 的静态登记：provider 名 ↔ 引擎 ENV 名 ↔ 平台兜底 settings 键。"""

    provider: str  # 稳定 provider 标识（也是 hasn_app_credential.config['provider'] 的值）
    env_name: str  # 引擎进程读的环境变量名（凭据值落这里）
    fallback_setting: str  # settings 上的平台兜底 key 属性名（无配置即空 → 省略）
    label: str  # 人类可读名（owner 看板展示用）


# ============================ 按 provider 族路由（doc22 §5 P7）============================
#
# 'gateway' = 经唤星 new-api 网关用主人自己的 relay token（OpenAI 兼容；OWNER 配额+计费+failover）。
# 'byo'     = 长尾**专有** API provider，主人自带 key（加密存 hasn_app_credential）/ 平台兜底。
#
# 决策依据：能套 OpenAI 兼容协议、且 new-api 已代理的族（OpenAI 系图像生成、TTS、STT，以及 new-api 支持
# 转发的视频）一律走网关——计费/配额/失败转移天然并入唤星账本，无需各 provider 单独对账。而**专有 API**
# 的长尾 provider（音乐 Suno、数字人 HeyGen、图像/视频 fal·Kling）不是 OpenAI 兼容、new-api 不代理 → BYO。
#
# 一个 provider 族只能 gateway 或 byo 二选一（不混）。同样是「图像」，OpenAI 兼容那条走 ``image``(gateway)，
# fal/Kling 专有那条单列 ``image_byo``(byo)——这样 ``resolve(families=None)`` 时两条都解析、互不挤占；
# 引擎只套用它那条工具真正读的 ENV（OpenAI 系工具读 OPENAI_*，fal 工具读 FAL_KEY），多下发无害。
_FAMILY_ROUTING: dict[str, str] = {
    'image': 'gateway',  # OpenAI 兼容文生图 / 图生图（DALL·E 等）：走网关（主人配额）
    'tts': 'gateway',  # 文本转语音：OpenAI 兼容 audio.speech，走网关
    'stt': 'gateway',  # 语音转文本：OpenAI 兼容 audio.transcriptions，走网关
    'video': 'gateway',  # 视频生成：new-api 支持转发的走网关（不支持的 provider 由引擎那条工具自报缺凭据）
    'image_byo': 'byo',  # 专有图像 provider（fal/Kling）：非 OpenAI 兼容，BYO
    'video_byo': 'byo',  # 专有视频 provider（fal/Kling）：非 OpenAI 兼容，BYO
    'music': 'byo',  # 音乐生成（Suno）：专有 API，BYO
    'avatar': 'byo',  # 数字人 / 口播（HeyGen）：专有 API，BYO
}

# BYO 族 → 该族下的长尾 provider 登记（一族可挂多个 provider；env_name 即引擎读的环境变量）。
_BYO_PROVIDERS: dict[str, tuple[ByoProvider, ...]] = {
    'image_byo': (ByoProvider('fal', 'FAL_KEY', 'MONTAGE_FALLBACK_FAL_KEY', 'fal.ai / Kling'),),
    'video_byo': (ByoProvider('fal', 'FAL_KEY', 'MONTAGE_FALLBACK_FAL_KEY', 'fal.ai / Kling'),),
    'music': (ByoProvider('suno', 'SUNO_API_KEY', 'MONTAGE_FALLBACK_SUNO_KEY', 'Suno'),),
    'avatar': (ByoProvider('heygen', 'HEYGEN_API_KEY', 'MONTAGE_FALLBACK_HEYGEN_KEY', 'HeyGen'),),
}

# 全部 BYO provider（去重，owner 看板/管理 CRUD 列举用）；以 provider 名为键。
_ALL_BYO_PROVIDERS: dict[str, ByoProvider] = {
    p.provider: p for plist in _BYO_PROVIDERS.values() for p in plist
}


def family_routing() -> dict[str, str]:
    """对外暴露族路由表的只读拷贝（owner 看板/测试用）。"""
    return dict(_FAMILY_ROUTING)


def all_byo_providers() -> list[ByoProvider]:
    """全部已登记的 BYO 长尾 provider（owner 看板列举凭据状态用）。"""
    return list(_ALL_BYO_PROVIDERS.values())


def get_byo_provider(provider: str) -> ByoProvider | None:
    """按 provider 名取登记项；未登记返回 None（不臆造）。"""
    return _ALL_BYO_PROVIDERS.get(provider)


def _gateway_families(families: list[str] | None) -> list[str]:
    target = families if families is not None else list(_FAMILY_ROUTING)
    return [f for f in target if _FAMILY_ROUTING.get(f) == 'gateway']


def _byo_families(families: list[str] | None) -> list[str]:
    target = families if families is not None else list(_FAMILY_ROUTING)
    return [f for f in target if _FAMILY_ROUTING.get(f) == 'byo']


# ============================ owner_hasn_id → 唤星 user_id ============================


async def _owner_user_id(db: AsyncSession, owner_hasn_id: str) -> int | None:
    """owner hasn_id（h_xxx）→ 唤星平台 user_id；无映射返回 None。"""
    human = await identity.get_human(db, hasn_id=owner_hasn_id)
    return int(human.user_id) if human and human.user_id else None


# ============================ (a) 网关路凭据 ============================


def _newapi_relay_base() -> str:
    """OpenAI 兼容 relay 基址（引擎设到 OPENAI_BASE_URL）。

    复用 hermes 给分身装 LLM 的同一 relay base（``HUANXING_HERMES_PLATFORM_LLM_BASE_URL``，
    形如 ``https://api.huanxing.ai/api/v1/llm/proxy/v1``）——经唤星 LLM 代理 → new-api 网关，
    用 relay token 的请求自动并入主人账本。这里**不**用 ``NEWAPI_ADMIN_BASE_URL``（那是 admin 管理 API，
    非 OpenAI 兼容 relay）。
    """
    return str(getattr(settings, 'HUANXING_HERMES_PLATFORM_LLM_BASE_URL', '') or '').rstrip('/')


async def _gateway_overrides(db: AsyncSession, *, owner_hasn_id: str) -> dict[str, str]:
    """网关族凭据：主人 new-api relay token + relay base。

    主人无 new-api 映射 / relay base 未配 → 返回空（优雅跳过，诚实，零 fake）。绝不在此铸 token
    （只读既有 ``llm_newapi_user_mapping``，铸 token 是 newapi service 的职责，登录/建分身时已完成）。
    """
    base = _newapi_relay_base()
    if not base:
        return {}
    user_id = await _owner_user_id(db, owner_hasn_id)
    if user_id is None:
        return {}
    mapping = await llm_newapi_user_mapping_dao.get_by_user(db, user_id)
    if mapping is None or not mapping.newapi_token_key:
        return {}
    # newapi_token_key 是裸 key（无 sk- 前缀，见 newapi service 落库注释）；relay 需 sk- 前缀。
    return {
        ENV_OPENAI_API_KEY: f'{_NEWAPI_KEY_PREFIX}{mapping.newapi_token_key}',
        ENV_OPENAI_BASE_URL: base,
    }


# ============================ (b) BYO 长尾凭据 ============================


async def _load_byo_row(db: AsyncSession, *, user_id: int, provider: str) -> HasnAppCredential | None:
    """取某主人某 provider 的 active BYO 凭据行（app_id='studio'，按 config.provider 匹配）。"""
    stmt = (
        sa.select(HasnAppCredential)
        .where(
            HasnAppCredential.app_id == STUDIO_APP_ID,
            HasnAppCredential.user_id == user_id,
            HasnAppCredential.config['provider'].astext == provider,
        )
        .order_by(HasnAppCredential.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


def _platform_fallback(provider: ByoProvider) -> str | None:
    """该 provider 的平台兜底 key（settings）；为空返回 None（不下发空串）。"""
    val = str(getattr(settings, provider.fallback_setting, '') or '').strip()
    return val or None


async def _byo_overrides(db: AsyncSession, *, owner_hasn_id: str, families: list[str] | None) -> dict[str, str]:
    """BYO 族凭据：逐 provider 解密主人自带 key；无则平台兜底；都无则省略。

    解密失败（密钥变更/损坏）= 诚实当作没有该 BYO，落到平台兜底（绝不抛、绝不记录密文）。
    """
    byo_families = _byo_families(families)
    if not byo_families:
        return {}
    user_id = await _owner_user_id(db, owner_hasn_id)

    # 该批族下的去重 provider（同一 provider 可跨族登记，如 fal 既 image 又 video）。
    providers: dict[str, ByoProvider] = {}
    for fam in byo_families:
        for p in _BYO_PROVIDERS.get(fam, ()):
            providers[p.provider] = p

    out: dict[str, str] = {}
    for provider in providers.values():
        value: str | None = None
        if user_id is not None:
            row = await _load_byo_row(db, user_id=user_id, provider=provider.provider)
            if row is not None and row.status == 'active' and row.credential_ref:
                try:
                    value = key_encryption.decrypt(row.credential_ref)
                except Exception:
                    logger.warning(
                        'studio BYO 凭据解密失败（密钥变更？）provider=%s owner=%s', provider.provider, owner_hasn_id
                    )
                    value = None
        if not value:
            value = _platform_fallback(provider)
        if value:  # 有 owner key 或平台兜底才下发；都没有则省略（引擎那条工具自报缺凭据）
            out[provider.env_name] = value
    return out


# ============================ 对外主入口 ============================


async def resolve_media_credentials(
    db: AsyncSession, *, owner_hasn_id: str, families: list[str] | None = None
) -> dict[str, str]:
    """按主人身份解析一张 ``{ENV_NAME: value}`` 临时凭据覆盖表，供引擎瞬时套用（绝不持久）。

    :param owner_hasn_id: 凭据归属主人（行级隔离键；绝不跨主人取凭据）。
    :param families: 需要的 provider 族子集（如 ['image','tts']）；None = 解析全部已配置族
        （引擎只套用它那条工具真正读的 ENV，多下发无害）。
    :return: ENV 覆盖表（可能为空——主人无任何可用凭据时诚实返回 {}，引擎据此报缺凭据，零 fake）。

    安全：返回值含明文凭据，**调用方绝不可记录/落库/回参**；仅作为请求体 ``credentials`` 字段下发引擎。
    """
    overrides: dict[str, str] = {}
    if _gateway_families(families):
        overrides.update(await _gateway_overrides(db, owner_hasn_id=owner_hasn_id))
    overrides.update(await _byo_overrides(db, owner_hasn_id=owner_hasn_id, families=families))
    return overrides


# ============================ BYO owner CRUD（owner-via-webui，非 agent 工具）============================
#
# 媒体凭据管理是「主人经 WebUI 操作」的能力，**不**开 MCP 工具 / agent scope（doc22 §5 P7）。
# 加密写 / 脱敏读 / 吊销，全部对齐 RAGFlow service key 的 key_encryption 模式。


async def list_byo_credentials(db: AsyncSession, *, owner_hasn_id: str) -> list[dict[str, str | bool | None]]:
    """列全部 BYO provider 的脱敏状态（**绝不**回明文/密文，只回 has_key + status + 更新时间）。"""
    user_id = await _owner_user_id(db, owner_hasn_id)
    rows_by_provider: dict[str, HasnAppCredential] = {}
    if user_id is not None:
        stmt = (
            sa.select(HasnAppCredential)
            .where(HasnAppCredential.app_id == STUDIO_APP_ID, HasnAppCredential.user_id == user_id)
            .order_by(HasnAppCredential.id.desc())
        )
        for credential_row in (await db.execute(stmt)).scalars().all():
            provider_key = str((credential_row.config or {}).get('provider') or '')
            if provider_key and provider_key not in rows_by_provider:  # 取每 provider 最新一行
                rows_by_provider[provider_key] = credential_row

    out: list[dict[str, str | bool | None]] = []
    for provider_spec in all_byo_providers():
        selected_credential = rows_by_provider.get(provider_spec.provider)
        has_owner_key = bool(
            selected_credential
            and selected_credential.status == 'active'
            and selected_credential.credential_ref
        )
        out.append(
            {
                'provider': provider_spec.provider,
                'label': provider_spec.label,
                'env_name': provider_spec.env_name,
                'status': (selected_credential.status if selected_credential else 'unset'),
                'has_key': has_owner_key,  # 主人自己是否配了 key（脱敏，绝不回值）
                'has_platform_fallback': _platform_fallback(provider_spec) is not None,
                'updated_time': (
                    selected_credential.updated_time.isoformat()
                    if selected_credential and selected_credential.updated_time
                    else None
                ),
            }
        )
    return out


async def upsert_byo_credential(
    db: AsyncSession, *, owner_hasn_id: str, provider: str, value: str
) -> dict[str, str | bool]:
    """主人配/换某 provider 的 BYO key：加密 upsert 进 hasn_app_credential（app_id='studio'）。

    value 为明文（仅此入参一处见明文，加密后即丢）；非法 provider / 空 value 报错（调用方信封化）。
    返回脱敏结果（绝不回明文/密文）。
    """
    spec = get_byo_provider(provider)
    if spec is None:
        raise errors.RequestError(msg=f'不支持的媒体 provider: {provider}')
    value = (value or '').strip()
    if not value:
        raise errors.RequestError(msg='凭据值不能为空')
    user_id = await _owner_user_id(db, owner_hasn_id)
    if user_id is None:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法配置媒体凭据')

    ciphertext = key_encryption.encrypt(value)
    row = await _load_byo_row(db, user_id=user_id, provider=provider)
    if row is None:
        row = HasnAppCredential(
            app_id=STUDIO_APP_ID,
            user_id=user_id,
            app_instance_id=0,  # studio BYO 不绑应用实例（cloud-brokered 单服务）
            credential_ref=ciphertext,
            status='active',
            last_error=None,
            config={'provider': provider, 'env_name': spec.env_name},
        )
        db.add(row)
    else:
        row.credential_ref = ciphertext
        row.status = 'active'
        row.last_error = None
        row.config = {**(row.config or {}), 'provider': provider, 'env_name': spec.env_name}
    await db.flush()
    return {'provider': provider, 'status': 'active', 'has_key': True}


async def revoke_byo_credential(db: AsyncSession, *, owner_hasn_id: str, provider: str) -> bool:
    """吊销主人某 provider 的 BYO key（清密文 + status=revoked）。返回是否命中一条 active 行。"""
    spec = get_byo_provider(provider)
    if spec is None:
        raise errors.RequestError(msg=f'不支持的媒体 provider: {provider}')
    user_id = await _owner_user_id(db, owner_hasn_id)
    if user_id is None:
        return False
    row = await _load_byo_row(db, user_id=user_id, provider=provider)
    if row is None or row.status != 'active':
        return False
    row.credential_ref = ''  # 清密文（绝不留）
    row.status = 'revoked'
    await db.flush()
    return True
