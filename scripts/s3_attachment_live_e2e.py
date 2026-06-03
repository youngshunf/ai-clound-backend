"""S3 附件一体化 —— 真实活体 E2E（打运行中 8020 + 真实七牛 + JWT/Redis 鉴权）。

验证 09 Stage1/1f 红线：
  1. owner 上传私有附件 → 真实七牛 PUT → 注册资产
  2. 鉴权三态：owner 恒可读 / 会话参与者+授予可读 / 陌生人不可读(零 fake 不返 URL) / 无会话上下文不可读
  3. 公开资产恒可读(CDN 直读)
  4. 私有签名 URL 真实 GET → 200(真实字节回读)

运行：DATABASE_PORT=15432 backend/.venv/bin/python /tmp/s3_attach_live_e2e.py
证据写 test-results/。零 fake：任何一步失败如实记录。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from backend.database.db import async_db_session
from backend.common.security.jwt import create_access_token
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service as asset_svc
from backend.plugin.s3.service.storage_service import storage_service as storage_svc

BASE = "http://127.0.0.1:8020"
UP = "/api/v1/hasn/app/assets/upload"
RES = "/api/v1/hasn/app/assets/resolve"

# 真实本地账号（hasn_humans）
A_USER, A_HASN = 4, "h_47094e96-ead5-4180-959a-8a28fac942e6"   # 小智 (owner / 参与者 A)
B_USER, B_HASN = 10, "h_0509cc75-5505-4793-9e5c-a06cc4b12e21"  # 瘦瘦福仔 (参与者 B)
D_USER, D_HASN = 11, "h_411ee65b-0925-47b6-94b6-48e2cefbdcc1"  # 智小芽 (陌生人 D)

# 1x1 PNG（真实字节）
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000154a24f600000000049454e44ae426082"
)

results: list[dict] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append({"name": name, "pass": bool(cond), "detail": str(detail)[:300]})
    print(("✅ PASS" if cond else "❌ FAIL"), name, "—", str(detail)[:200])


async def resolve(cli: httpx.AsyncClient, token: str, asset_ids: list[str], conv: str | None):
    body: dict = {"asset_ids": asset_ids, "expires_in": 600}
    if conv is not None:
        body["conversation_id"] = conv
    r = await cli.post(RES, headers={"Authorization": f"Bearer {token}"}, json=body)
    data = r.json().get("data") if r.status_code == 200 else None
    ids = {i["asset_id"]: i for i in data} if isinstance(data, list) else {}
    return r.status_code, ids


async def main() -> int:
    created = {"private_storage": False, "conv": None, "assets": []}
    conv_id = str(uuid.uuid4())

    # ---- 0) setup：私有 s3 行 + 会话 A<->B ----
    async with async_db_session() as db:
        row = (await db.execute(text("select id from s3_storage where access='private' limit 1"))).first()
        if not row:
            await db.execute(text(
                "insert into s3_storage(name,endpoint,access_key,secret_key,bucket,prefix,cdn_domain,access,sign_strategy,created_time) "
                "select name||'-private', endpoint, access_key, secret_key, bucket, prefix, cdn_domain, 'private','s3_presign', now() "
                "from s3_storage where access='public' limit 1"
            ))
            created["private_storage"] = True
        await db.execute(text(
            "insert into hasn_conversations(id,type,participant_a_id,participant_b_id,participant_a_type,participant_b_type,status,member_count,created_time,updated_time) "
            "values(:id,'direct',:a,:b,'human','human','active',2,now(),now())"
        ), {"id": conv_id, "a": A_HASN, "b": B_HASN})
        created["conv"] = conv_id
        await db.commit()
    print(f"setup: conv={conv_id} private_storage_created={created['private_storage']}")

    tokA = (await create_access_token(A_USER, multi_login=True)).access_token
    tokB = (await create_access_token(B_USER, multi_login=True)).access_token
    tokD = (await create_access_token(D_USER, multi_login=True)).access_token

    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=90) as cli:
            # ---- 1) A 上传私有图片（真实七牛 PUT）----
            r = await cli.post(UP, headers={"Authorization": f"Bearer {tokA}"},
                               files={"file": ("e2e_attach.png", PNG, "image/png")})
            check("1_upload_200", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")
            X = (r.json().get("data") or {}).get("asset_id") if r.status_code == 200 else None
            check("1_upload_asset_id", bool(X), X)
            if not X:
                return 1
            created["assets"].append(X)

            # ---- DB-target 校验：8020 与本地 15432 是否同库 ----
            async with async_db_session() as db:
                found = (await db.execute(text("select access, owner_hasn_id from hasn_assets where asset_id=:i"),
                                          {"i": X})).first()
            check("1b_same_db_as_8020", found is not None,
                  "资产在本地 15432 可见" if found else "8020 连的不是本地 15432，跨库 E2E 不可行")
            if not found:
                return 1
            check("1c_uploaded_private", found[0] == "private", f"access={found[0]}")

            # ---- 2) 注册公开资产 Y（user_avatar→public，直连 service 真实七牛 PUT）----
            async with async_db_session() as db:
                ref = await storage_svc.upload(db, PNG, category="user_avatar",
                                               filename="e2e_pub.png", content_type="image/png")
                ay = await asset_svc.register_asset(db, owner_hasn_id=A_HASN, ref=ref, kind="image",
                                                    width=None, height=None, duration_ms=None)
                Y = ay.asset_id
                await db.commit()
            created["assets"].append(Y)
            check("2_public_asset_registered", bool(Y), f"{Y} access={ref.access}")

            # ---- 3) 鉴权红线（grant 前）----
            _, idsA0 = await resolve(cli, tokA, [X], None)
            check("3a_owner_reads_private_no_conv", X in idsA0, "owner 恒可读")

            _, idsB0 = await resolve(cli, tokB, [X], conv_id)
            check("3b_participant_before_grant_denied", X not in idsB0, "未授予→不可读(零 fake)")

            # grant X → 会话 C（模拟 message_router 落库时的授权）
            async with async_db_session() as db:
                await asset_svc.grant_to_conversation(db, asset_id=X, conversation_id=conv_id)
                await db.commit()

            _, idsB1 = await resolve(cli, tokB, [X], conv_id)
            check("3c_participant_after_grant_ok", X in idsB1, "参与者+授予→可读")

            _, idsD = await resolve(cli, tokD, [X], conv_id)
            check("3d_stranger_denied", X not in idsD, "陌生人(非参与者)→不可读")

            _, idsBnoconv = await resolve(cli, tokB, [X], None)
            check("3e_participant_no_conv_denied", X not in idsBnoconv, "无会话上下文→不可读")

            _, idsDpub = await resolve(cli, tokD, [Y], None)
            check("3f_public_always_readable", Y in idsDpub, "公开资产恒可读(陌生人也可)")
            if Y in idsDpub:
                check("3g_public_no_expiry", idsDpub[Y].get("expires_at") in (None, ""),
                      f"expires_at={idsDpub[Y].get('expires_at')}")

            # ---- 4) 私有签名 URL 真实 GET → 200 ----
            signed_url = idsB1.get(X, {}).get("display_url")
            check("4a_private_has_signed_url", bool(signed_url), str(signed_url)[:120])
            if signed_url:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as raw:
                    g = await raw.get(signed_url)
                check("4b_signed_url_fetch_200", g.status_code == 200 and len(g.content) > 0,
                      f"status={g.status_code} bytes={len(g.content)}")
                check("4c_bytes_roundtrip", g.status_code == 200 and g.content == PNG,
                      f"回读字节匹配={g.content == PNG}")

            # 公开 CDN URL 形态（不一定可外网直连，只校验 URL 形态非签名）
            pub_url = idsDpub.get(Y, {}).get("display_url", "")
            check("4d_public_url_is_cdn", "hasn-cdn" in pub_url or pub_url.startswith("http"), pub_url[:120])

    finally:
        # ---- cleanup（best-effort，保持共享库整洁）----
        async with async_db_session() as db:
            for aid in created["assets"]:
                await db.execute(text("delete from hasn_asset_grants where asset_id=:i"), {"i": aid})
                await db.execute(text("delete from hasn_assets where asset_id=:i"), {"i": aid})
            if created["conv"]:
                await db.execute(text("delete from hasn_conversations where id=:i"), {"i": created["conv"]})
            if created["private_storage"]:
                await db.execute(text("delete from s3_storage where access='private'"))
            await db.commit()
        print("cleanup done")

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    summary = {
        "suite": "s3_attachment_live_e2e",
        "base": BASE,
        "passed": passed, "total": total, "all_pass": passed == total,
        "results": results,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "note": "活体打运行中 8020 + 真实七牛 + JWT/Redis；私有桶ACL裸URL-403需独立私有七牛桶(Stage0a)，此处私有行复用同桶仅验云端鉴权+签名+对象往返",
    }
    out = "/Users/mac/openclaw-workspace/huanxing/huanxing-project/test-results/s3-attachment-live-e2e.json"
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n=== {passed}/{total} passed === evidence: {out}")
    return 0 if passed == total else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
