# -*- coding: utf-8 -*-
"""一次性数据迁移：知识库（RAGFlow）实例/凭据 → 通用应用平台底座。

实施 14-AI-Native应用平台/实施/03 §3 P2。

    hasn_ragflow_instance   → hasn_app_instance(app_id='knowledge')
    hasn_ragflow_credential → hasn_app_credential(app_id='knowledge')

要点
- 幂等：实例键 (app_id, scope, enterprise_id)；凭据键 (user_id, app_instance_id)。复跑显示「无变更」。
- 加密收编：admin/api key 密文（secret_crypto/bytea）→ 解密 → key_encryption 再加密 → credential_ref。
  （注：secret_crypto 底层即 key_encryption，明文逐条一致性必然成立，仍逐条断言以防回归。）
- RAGFlow 私有字段下沉 config：实例 {public_pem, default_embd_id, default_llm_id}；凭据 {ragflow_user_id, ragflow_tenant_id}。
- 双 active public 护栏：hasn_app_instance 的 partial unique 只允许一条 public/app_id。
  迁移前显式二选一（默认保留「绑定 active 凭据最多」者，tie-break created_time DESC；或 --keep-public-url 指定）；
  另一条 public 实例丢弃，绑在被丢实例上的凭据一并跳过并打印（零静默）。
- 校验：逐条 key_encryption.decrypt(new.credential_ref) == decrypt_ragflow_secret(old.*_encrypted)；count 对齐。
- 默认 --dry-run（全程真实 upsert 但事务回滚，只打印计划+校验）；--commit 才落库。

用法
    uv run python scripts/migrate_knowledge_to_app_instance.py                          # dry-run
    uv run python scripts/migrate_knowledge_to_app_instance.py --commit                 # 落库
    uv run python scripts/migrate_knowledge_to_app_instance.py --keep-public-url URL    # 显式指定保留的 public 实例
"""

from __future__ import annotations

import argparse
import asyncio

import sqlalchemy as sa

from sqlalchemy.orm import DeclarativeBase

from backend.app.hasn.model import HasnAppCredential, HasnAppInstance
from backend.app.hasn.util.secret_crypto import decrypt_ragflow_secret
from backend.app.llm.core.encryption import key_encryption
from backend.database.db import async_db_session


# 旧表 hasn_ragflow_instance / hasn_ragflow_credential 的「只读 schema 快照」。
# 实施 03 P5 已删除这两张表的 ORM 模型（model/schema/crud），但本一次性迁移脚本
# 在「删表前」仍需读它们搬数据 —— 故自带最小映射，解除对已删模型的 import 依赖
# （独立 DeclarativeBase，不污染应用共享 metadata；仅映射本脚本读取的列）。
# 用经典 sa.Column 声明（非 Mapped[] 注解）：本脚本顶部有 `from __future__ import
# annotations`，会把注解变字符串，独立 DeclarativeBase 解析 Mapped[] 易失败；只读
# 搬数据用经典列最稳。
class _LegacyBase(DeclarativeBase):
    pass


class HasnRagflowInstance(_LegacyBase):
    """旧 RAGFlow 实例表只读映射（迁移自带快照）。"""

    __tablename__ = 'hasn_ragflow_instance'

    id = sa.Column(sa.BigInteger, primary_key=True)
    scope = sa.Column(sa.String(16))
    enterprise_id = sa.Column(sa.BigInteger)
    url = sa.Column(sa.String(512))
    admin_api_key_encrypted = sa.Column(sa.LargeBinary)
    public_pem = sa.Column(sa.Text)
    default_embd_id = sa.Column(sa.String(128))
    default_llm_id = sa.Column(sa.String(128))
    status = sa.Column(sa.String(16))
    created_time = sa.Column(sa.DateTime(timezone=True))


class HasnRagflowCredential(_LegacyBase):
    """旧 RAGFlow 用户凭据表只读映射（迁移自带快照）。"""

    __tablename__ = 'hasn_ragflow_credential'

    id = sa.Column(sa.BigInteger, primary_key=True)
    user_id = sa.Column(sa.BigInteger)
    instance_id = sa.Column(sa.BigInteger)
    ragflow_user_id = sa.Column(sa.String(64))
    ragflow_tenant_id = sa.Column(sa.String(64))
    api_key_encrypted = sa.Column(sa.LargeBinary)
    status = sa.Column(sa.String(16))
    last_error = sa.Column(sa.Text)
    created_time = sa.Column(sa.DateTime(timezone=True))


APP_ID = 'knowledge'
# UI 默认 transport（不改调用形态，见实施 03 §0.3：knowledge.search 仍 daemon_direct 直连）
TRANSPORT_DEFAULT = 'daemon_direct'


def _reencrypt(plaintext: str) -> str:
    """明文 → key_encryption 密文（空明文 → 空串，与 hasn_app_credential.credential_ref DEFAULT '' 一致）。"""
    return key_encryption.encrypt(plaintext) if plaintext else ''


def _decrypt_ref(ref: str | None) -> str:
    return key_encryption.decrypt(ref) if ref else ''


def _instance_config(inst: HasnRagflowInstance) -> dict:
    cfg: dict = {}
    if inst.public_pem:
        cfg['public_pem'] = inst.public_pem
    if inst.default_embd_id:
        cfg['default_embd_id'] = inst.default_embd_id
    if inst.default_llm_id:
        cfg['default_llm_id'] = inst.default_llm_id
    return cfg


def _credential_config(cred: HasnRagflowCredential) -> dict:
    cfg: dict = {}
    if cred.ragflow_user_id:
        cfg['ragflow_user_id'] = cred.ragflow_user_id
    if cred.ragflow_tenant_id:
        cfg['ragflow_tenant_id'] = cred.ragflow_tenant_id
    return cfg


async def _pick_public_to_keep(
    db, public_actives: list[HasnRagflowInstance], keep_public_url: str | None
) -> HasnRagflowInstance | None:
    if not public_actives:
        return None
    if len(public_actives) == 1:
        return public_actives[0]
    if keep_public_url:
        for inst in public_actives:
            if inst.url == keep_public_url:
                return inst
        raise SystemExit(f'❌ --keep-public-url {keep_public_url!r} 未匹配任何 active public 实例')
    # 默认：绑定 active 凭据最多者；tie-break created_time DESC
    counts: dict[int, int] = {}
    for inst in public_actives:
        counts[inst.id] = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(HasnRagflowCredential)
                .where(
                    HasnRagflowCredential.instance_id == inst.id,
                    HasnRagflowCredential.status == 'active',
                )
            )
        ).scalar_one()
    return sorted(public_actives, key=lambda i: (counts[i.id], i.created_time), reverse=True)[0]


async def _find_app_instance(db, *, scope: str, enterprise_id: int | None) -> HasnAppInstance | None:
    stmt = sa.select(HasnAppInstance).where(
        HasnAppInstance.app_id == APP_ID, HasnAppInstance.scope == scope
    )
    if scope == 'enterprise':
        stmt = stmt.where(HasnAppInstance.enterprise_id == enterprise_id)
    else:
        stmt = stmt.where(HasnAppInstance.enterprise_id.is_(None))
    return (await db.execute(stmt)).scalars().first()


async def _upsert_app_instance(
    db, old: HasnRagflowInstance, *, credential_ref: str, config: dict
) -> tuple[int, str]:
    """返回 (new_app_instance_id, action)。action ∈ {created, updated, unchanged}。"""
    endpoint = old.url or None
    enterprise_id = old.enterprise_id if old.scope == 'enterprise' else None
    existing = await _find_app_instance(db, scope=old.scope, enterprise_id=enterprise_id)
    if existing is None:
        row = HasnAppInstance(
            app_id=APP_ID,
            scope=old.scope,
            enterprise_id=enterprise_id,
            endpoint=endpoint,
            transport_default=TRANSPORT_DEFAULT,
            credential_ref=credential_ref,
            status=old.status,
            config=config,
        )
        row.created_time = old.created_time  # 保留源 created_time（resolver 排序依赖）
        db.add(row)
        await db.flush()
        return row.id, 'created'

    unchanged = (
        existing.endpoint == endpoint
        and existing.status == old.status
        and existing.transport_default == TRANSPORT_DEFAULT
        and existing.config == config
        and _decrypt_ref(existing.credential_ref) == _decrypt_ref(credential_ref)
    )
    if unchanged:
        return existing.id, 'unchanged'
    existing.endpoint = endpoint
    existing.transport_default = TRANSPORT_DEFAULT
    existing.credential_ref = credential_ref
    existing.status = old.status
    existing.config = config
    await db.flush()
    return existing.id, 'updated'


async def _upsert_app_credential(
    db, old: HasnRagflowCredential, *, app_instance_id: int, credential_ref: str, config: dict
) -> str:
    """返回 action ∈ {created, updated, unchanged}。"""
    existing = (
        await db.execute(
            sa.select(HasnAppCredential).where(
                HasnAppCredential.user_id == old.user_id,
                HasnAppCredential.app_instance_id == app_instance_id,
            )
        )
    ).scalars().first()
    if existing is None:
        row = HasnAppCredential(
            app_id=APP_ID,
            user_id=old.user_id,
            app_instance_id=app_instance_id,
            credential_ref=credential_ref,
            status=old.status,
            last_error=old.last_error,
            config=config,
        )
        row.created_time = old.created_time
        db.add(row)
        await db.flush()
        return 'created'

    unchanged = (
        existing.status == old.status
        and (existing.last_error or None) == (old.last_error or None)
        and existing.config == config
        and _decrypt_ref(existing.credential_ref) == _decrypt_ref(credential_ref)
    )
    if unchanged:
        return 'unchanged'
    existing.credential_ref = credential_ref
    existing.status = old.status
    existing.last_error = old.last_error
    existing.config = config
    await db.flush()
    return 'updated'


async def migrate(*, commit: bool, keep_public_url: str | None) -> int:
    async with async_db_session() as db:
        instances = (await db.execute(sa.select(HasnRagflowInstance))).scalars().all()
        public_actives = [i for i in instances if i.scope == 'public' and i.status == 'active']
        keep_public = await _pick_public_to_keep(db, public_actives, keep_public_url)

        print('=' * 70)
        print(f'知识库 → 应用平台底座迁移（{"COMMIT" if commit else "DRY-RUN"}）  app_id={APP_ID!r}')
        print('=' * 70)
        if keep_public is not None:
            print(f'保留 public 实例: id={keep_public.id} url={keep_public.url!r} created_time={keep_public.created_time}')
        dropped_public = [i for i in public_actives if keep_public is None or i.id != keep_public.id]
        for i in dropped_public:
            print(f'⚠️  丢弃多余 public 实例: id={i.id} url={i.url!r}（partial unique 只允许一条 public/app_id）')

        # 待迁移实例集：保留的 public + 全部 enterprise
        to_migrate = [
            i
            for i in instances
            if (i.scope == 'public' and keep_public is not None and i.id == keep_public.id)
            or (i.scope != 'public')
        ]
        mapping: dict[int, int] = {}  # old_instance_id -> new_app_instance_id
        inst_actions = {'created': 0, 'updated': 0, 'unchanged': 0}
        print('\n-- 实例 --')
        for inst in to_migrate:
            plaintext = decrypt_ragflow_secret(inst.admin_api_key_encrypted)
            ref = _reencrypt(plaintext)
            assert _decrypt_ref(ref) == plaintext, f'实例 {inst.id} 明文一致性校验失败'
            cfg = _instance_config(inst)
            new_id, action = await _upsert_app_instance(db, inst, credential_ref=ref, config=cfg)
            mapping[inst.id] = new_id
            inst_actions[action] += 1
            print(
                f'  [{action:9s}] old_instance_id={inst.id} scope={inst.scope} '
                f'enterprise_id={inst.enterprise_id} -> app_instance_id={new_id} '
                f'(adminkey_plaintext_len={len(plaintext)}, config_keys={sorted(cfg)})'
            )

        # 凭据：仅迁移其实例仍存活者
        credentials = (await db.execute(sa.select(HasnRagflowCredential))).scalars().all()
        cred_actions = {'created': 0, 'updated': 0, 'unchanged': 0}
        skipped = []
        print('\n-- 凭据 --')
        for cred in credentials:
            if cred.instance_id not in mapping:
                skipped.append(cred)
                print(
                    f'  [skipped  ] cred_id={cred.id} user_id={cred.user_id} '
                    f'old_instance_id={cred.instance_id}（实例已被丢弃，凭据不迁移）'
                )
                continue
            plaintext = decrypt_ragflow_secret(cred.api_key_encrypted)
            ref = _reencrypt(plaintext)
            assert _decrypt_ref(ref) == plaintext, f'凭据 {cred.id} 明文一致性校验失败'
            cfg = _credential_config(cred)
            action = await _upsert_app_credential(
                db, cred, app_instance_id=mapping[cred.instance_id], credential_ref=ref, config=cfg
            )
            cred_actions[action] += 1
            print(
                f'  [{action:9s}] cred_id={cred.id} user_id={cred.user_id} '
                f'-> app_instance_id={mapping[cred.instance_id]} status={cred.status} '
                f'(apikey_plaintext_len={len(plaintext)}, config_keys={sorted(cfg)})'
            )

        # 最终一致性复核（读回新表逐条解密对账）
        print('\n-- 校验（读回新表逐条解密对账）--')
        ok = True
        for inst in to_migrate:
            new = await db.get(HasnAppInstance, mapping[inst.id])
            if _decrypt_ref(new.credential_ref) != decrypt_ragflow_secret(inst.admin_api_key_encrypted):
                ok = False
                print(f'  ❌ 实例 {inst.id} → {new.id} admin key 明文不一致')
        for cred in credentials:
            if cred.instance_id not in mapping:
                continue
            new = (
                await db.execute(
                    sa.select(HasnAppCredential).where(
                        HasnAppCredential.user_id == cred.user_id,
                        HasnAppCredential.app_instance_id == mapping[cred.instance_id],
                    )
                )
            ).scalars().first()
            if new is None or _decrypt_ref(new.credential_ref) != decrypt_ragflow_secret(cred.api_key_encrypted):
                ok = False
                print(f'  ❌ 凭据 {cred.id} 明文不一致或缺失')
        print('  ✓ 全部明文逐条一致' if ok else '  ❌ 校验未通过')

        print('\n-- 汇总 --')
        print(f'  实例: {inst_actions}（源 {len(instances)} 条，迁移 {len(to_migrate)} 条，丢弃 public {len(dropped_public)} 条）')
        print(f'  凭据: {cred_actions}（源 {len(credentials)} 条，跳过 {len(skipped)} 条）')

        if not ok:
            await db.rollback()
            print('\n❌ 校验失败，已回滚，未写入任何数据。')
            return 1
        if commit:
            await db.commit()
            print('\n✅ 已 COMMIT 落库。')
        else:
            await db.rollback()
            print('\n🟡 DRY-RUN：已回滚，未写入。加 --commit 落库。')
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='知识库实例/凭据 → 应用平台底座 一次性迁移')
    parser.add_argument('--commit', action='store_true', help='落库（默认 dry-run 只打印+校验）')
    parser.add_argument('--keep-public-url', default=None, help='双 active public 时显式指定保留哪条（按 url 匹配）')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(migrate(commit=args.commit, keep_public_url=args.keep_public_url)))


if __name__ == '__main__':
    main()
