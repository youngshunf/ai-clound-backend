"""统一视频引擎媒体凭据解析 + BYO 管理 + 计量真实 PG 测试（doc22 §5 P7，零 mock）。

对齐 test_studio_resource_share 的真 PG 直插行 + flush(不 commit) + rollback 模式。覆盖：
(a) 网关族凭据：有 new-api 映射 → 产出 OPENAI_API_KEY/OPENAI_BASE_URL 覆盖表；无映射 → 优雅省略。
(b) BYO：POST 凭据 → DB 里密文 != 明文；resolve 解密进 ENV 表；无 BYO 落平台兜底；list 绝不回明文；DELETE 吊销。
计量：渲染 job 从引擎 cost 结果落 studio_render_job.cost（_build_cost）。

凭据按 owner 隔离：用唯一 hasn_id/user_id 标签建主人，绝不跨主人取凭据。
"""

from __future__ import annotations

import asyncio
import random

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from _pytest.monkeypatch import MonkeyPatch
    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnAppCredential
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_studio.service import media_credentials as mc
from backend.app.hasn_studio.service.studio_service import _build_cost
from backend.common.exception import errors
from backend.common.security.encryption import key_encryption
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _uid() -> int:
    """唯一 user_id（避开真实账号；测试 rollback 不落库）。"""
    return random.randint(900_000_000, 999_999_999)


async def _make_owner(session: AsyncSession, *, user_id: int) -> str:
    """落一条 hasn_humans 行（owner_hasn_id ↔ user_id 映射），返回 owner_hasn_id。

    star_id 有唯一约束（且本地已存空串行），故按 user_id 给唯一值。
    """
    hasn_id = f'h_p7_{user_id}'
    session.add(
        HasnHumans(hasn_id=hasn_id, star_id=f'p7{user_id}', user_id=user_id, nickname=f'P7_{user_id}', status='active')
    )
    await session.flush()
    return hasn_id


async def _seed_newapi_mapping(session: AsyncSession, *, user_id: int, token_key: str) -> None:
    """直插 llm_newapi_user_mapping 行（owner 有 new-api relay token），用 raw SQL 避开 dataclass init 约束。"""
    await session.execute(
        text(
            'insert into llm_newapi_user_mapping '
            '(huanxing_user_id, newapi_user_id, newapi_token_key, newapi_token_id, app_code, status) '
            "values (:uid, :nid, :key, :tid, 'huanxing', 'active')"
        ),
        {'uid': user_id, 'nid': user_id, 'key': token_key, 'tid': user_id},
    )
    await session.flush()


# ============================ (a) 网关族凭据解析 ============================


async def test_gateway_overrides_with_newapi_mapping(session: AsyncSession) -> None:
    """主人有 new-api 映射 → 网关族解析出 OPENAI_API_KEY(sk-<token>) + OPENAI_BASE_URL。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    await _seed_newapi_mapping(session, user_id=uid, token_key='tk_p7_gw')

    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['image'])
    assert env[mc.ENV_OPENAI_API_KEY] == 'sk-tk_p7_gw'  # 裸 key 加 sk- 前缀
    assert env[mc.ENV_OPENAI_BASE_URL]  # relay base（settings 默认非空）


async def test_gateway_overrides_omitted_without_mapping(session: AsyncSession) -> None:
    """主人无 new-api 映射 → 网关族优雅省略（诚实，零 fake），ENV 表里无 OPENAI_*。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)  # 不 seed newapi mapping

    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['image'])
    assert mc.ENV_OPENAI_API_KEY not in env
    assert mc.ENV_OPENAI_BASE_URL not in env


# ============================ (b) BYO 凭据：写密文 / 解密 / 兜底 / 脱敏 / 吊销 ============================


async def test_byo_upsert_encrypts_in_db(session: AsyncSession) -> None:
    """POST BYO 凭据 → DB credential_ref 是密文（!= 明文）且可解回原文；config.provider 落对。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    plaintext = 'fal-secret-XYZ-789'

    res = await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='fal', value=plaintext)
    assert res['status'] == 'active' and res['has_key'] is True

    row = (
        await session.execute(
            select(HasnAppCredential).where(
                HasnAppCredential.app_id == 'studio',
                HasnAppCredential.user_id == uid,
                HasnAppCredential.config['provider'].astext == 'fal',
            )
        )
    ).scalar_one()
    assert row.credential_ref and row.credential_ref != plaintext  # 落库即密文，绝不明文
    assert key_encryption.decrypt(row.credential_ref) == plaintext  # 可解回
    assert row.config['env_name'] == 'FAL_KEY'


async def test_byo_resolve_decrypts_into_env(session: AsyncSession) -> None:
    """配了 BYO fal key → resolve 把它解密进 FAL_KEY（image_byo 专有族；music 族独立、不串）。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='fal', value='fal-live-key')

    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['image_byo'])
    assert env['FAL_KEY'] == 'fal-live-key'
    # music 族（suno）没配也没平台兜底（测试环境 settings 为空）→ 不下发
    env_music = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['music'])
    assert 'SUNO_API_KEY' not in env_music


async def test_gateway_and_byo_families_coexist(session: AsyncSession) -> None:
    """families=None 解析全部：网关族出 OPENAI_*（有 newapi 映射时）+ BYO 专有族出各自 env，互不挤占。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    await _seed_newapi_mapping(session, user_id=uid, token_key='tk_both')
    await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='fal', value='fal-both')

    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=None)
    assert env[mc.ENV_OPENAI_API_KEY] == 'sk-tk_both'  # 网关族（image/tts/stt/video）
    assert env['FAL_KEY'] == 'fal-both'  # BYO 专有族（image_byo/video_byo）同时在场


async def test_byo_platform_fallback_when_no_owner_key(session: AsyncSession, monkeypatch: MonkeyPatch) -> None:
    """主人没配某 provider BYO → 用平台兜底 settings key（运营自费）。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    monkeypatch.setattr(mc.settings, 'MONTAGE_FALLBACK_SUNO_KEY', 'platform-suno-fallback', raising=False)

    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['music'])
    assert env['SUNO_API_KEY'] == 'platform-suno-fallback'


async def test_byo_owner_key_overrides_platform_fallback(session: AsyncSession, monkeypatch: MonkeyPatch) -> None:
    """主人配了 BYO → 优先用主人自己的 key，而非平台兜底。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    monkeypatch.setattr(mc.settings, 'MONTAGE_FALLBACK_SUNO_KEY', 'platform-suno-fallback', raising=False)
    await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='suno', value='owner-own-suno')

    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['music'])
    assert env['SUNO_API_KEY'] == 'owner-own-suno'  # 主人自带优先于平台兜底


async def test_list_byo_never_returns_plaintext(session: AsyncSession) -> None:
    """list_byo_credentials 只回脱敏状态（has_key/status），绝不含明文/密文。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    secret = 'heygen-top-secret'
    await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='heygen', value=secret)

    items = await mc.list_byo_credentials(session, owner_hasn_id=owner)
    by_provider = {it['provider']: it for it in items}
    heygen = by_provider['heygen']
    assert heygen['has_key'] is True and heygen['status'] == 'active'
    # 全表序列化里绝不出现明文或密文
    blob = repr(items)
    assert secret not in blob
    row = (
        await session.execute(
            select(HasnAppCredential).where(
                HasnAppCredential.user_id == uid, HasnAppCredential.config['provider'].astext == 'heygen'
            )
        )
    ).scalar_one()
    assert row.credential_ref not in blob  # 密文也绝不出现在列表响应


async def test_byo_revoke(session: AsyncSession) -> None:
    """DELETE 吊销 → status=revoked + 清密文 + resolve 不再下发（落平台兜底，此处无兜底则省略）。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='fal', value='to-be-revoked')

    assert await mc.revoke_byo_credential(session, owner_hasn_id=owner, provider='fal') is True
    row = (
        await session.execute(
            select(HasnAppCredential).where(
                HasnAppCredential.user_id == uid, HasnAppCredential.config['provider'].astext == 'fal'
            )
        )
    ).scalar_one()
    assert row.status == 'revoked' and not row.credential_ref  # 密文已清
    env = await mc.resolve_media_credentials(session, owner_hasn_id=owner, families=['image_byo'])
    assert 'FAL_KEY' not in env  # 吊销后不下发（无平台兜底）
    # 再次吊销 → 无 active 行命中
    assert await mc.revoke_byo_credential(session, owner_hasn_id=owner, provider='fal') is False


async def test_byo_rejects_unknown_provider(session: AsyncSession) -> None:
    """未登记 provider → upsert/revoke 报 RequestError（不臆造、不静默）。"""
    uid = _uid()
    owner = await _make_owner(session, user_id=uid)
    with pytest.raises(errors.RequestError):
        await mc.upsert_byo_credential(session, owner_hasn_id=owner, provider='bogus', value='x')
    with pytest.raises(errors.RequestError):
        await mc.revoke_byo_credential(session, owner_hasn_id=owner, provider='bogus')


async def test_owner_isolation(session: AsyncSession) -> None:
    """凭据按 owner 隔离：A 配的 fal key 绝不出现在 B 的解析/列表里。"""
    uid_a, uid_b = _uid(), _uid()
    owner_a = await _make_owner(session, user_id=uid_a)
    owner_b = await _make_owner(session, user_id=uid_b)
    await mc.upsert_byo_credential(session, owner_hasn_id=owner_a, provider='fal', value='A-only-key')

    env_b = await mc.resolve_media_credentials(session, owner_hasn_id=owner_b, families=['image_byo'])
    assert 'FAL_KEY' not in env_b  # B 没配且无兜底 → 拿不到 A 的
    items_b = await mc.list_byo_credentials(session, owner_hasn_id=owner_b)
    assert all(it['has_key'] is False for it in items_b)  # B 名下无任何 key


# ============================ 计量（metering）============================


async def test_metering_captures_engine_cost() -> None:
    """_build_cost 如实落引擎返回的 cost（total_usd/provider_usage/gpu_sec）+ duration_sec；不臆造。

    （_build_cost 是纯同步函数；async 仅为对齐模块级 pytestmark，sleep(0) 让其确为协程。）
    """
    await asyncio.sleep(0)
    snapshot = {
        'status': 'succeeded',
        'duration_sec': 42.0,
        'cost': {'total_usd': 1.25, 'gpu_sec': 0, 'provider_usage': {'fal': {'images': 3}}},
    }
    cost = _build_cost(snapshot)
    assert cost is not None
    assert cost['duration_sec'] == pytest.approx(42.0)
    assert cost['total_usd'] == pytest.approx(1.25)
    assert cost['provider_usage'] == {'fal': {'images': 3}}


async def test_metering_none_when_no_cost() -> None:
    """引擎没给任何成本字段 → cost 为 None（不造空对象，诚实）。"""
    await asyncio.sleep(0)
    assert _build_cost({'status': 'running'}) is None
