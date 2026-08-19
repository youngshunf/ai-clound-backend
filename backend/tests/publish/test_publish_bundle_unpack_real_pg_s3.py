"""发布时物化（bundle-zip 解包 + referenced 资产 server-side copy）真实 PG + 真实 S3 集成测试。

背景回归一：发布侧曾缺失「解包逐对象」环节——daemon 上传的 bundle-zip manifest
（`{entry, assets[]}`，无 object_key）被原样存库，serve 侧 `_bundle_entry` 按
`{files: {name: {object_key}}}` 取对象恒 miss，`/s/{slug}` 恒 410「bundle 缺少入口 index.html」。

背景回归二（资产引用化）：deck 页里的图本来就在对象存储桶里，旧链路却把它当外部
http 资源「下载回 daemon → 打进 zip → 上传回同桶 → 解包再写一遍」。新契约下 daemon
打包核心对 hasn://asset 只登记引用（`status='referenced'` + `asset_id`），云端发布时
做同桶 server-side copy，零公网流量。

覆盖（DoD）：
  - `_is_safe_bundle_member` 纯函数：放行干净相对路径，拒绝绝对路径/反斜杠/`.`/`..`/空段
  - `_unpack_bundle_zip_files`：真实 zip → 逐对象写回同 storage → files 条目
    （object_key 可经 storage_service 读回同样字节，index.html mime=text/html）
  - referenced 物化：同桶 copy 后字节与原子对象一致、**原子对象仍在**（copy 不是 move）、
    files 条目落在发布前缀下
  - ACL：referenced 资产不属于发布者 → 显式报错（防把别人私图挂上自己的公开站点）
  - 契约错误如实报：非 zip、成员路径穿越、缺 index.html 均抛错，不产出残缺 manifest
  - `create_site` / `update_site` 接入：runtime='bundle-zip' 或带 referenced 资产时
    revision.manifest_json 落 files 格式

需要 export DATABASE_PORT=15432（本机 dev PG，其 s3_storage 指向真实七牛私桶）。
对象存储写入无法随事务回滚，测试末尾显式 delete_object 清理。
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from sqlalchemy import text

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_publish.service.publish_service import (
    _is_safe_bundle_member,
    _materialize_referenced_assets,
    _unpack_bundle_zip_files,
    publish_service,
)
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import ObjectRef, storage_service
from backend.plugin.s3.utils.file_ops import write_bytes

_PRIVATE_STORAGE_ID = 1  # dev 库 s3_storage 的私桶行（与生产同形状）


def _make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ---------- 纯函数（无 DB） ----------


def test_safe_bundle_member_accepts_clean_relative_paths() -> None:
    assert _is_safe_bundle_member('index.html') is True
    assert _is_safe_bundle_member('assets/a-b_c.png') is True


def test_safe_bundle_member_rejects_traversal_and_absolute() -> None:
    assert _is_safe_bundle_member('/etc/passwd') is False
    assert _is_safe_bundle_member('../x') is False
    assert _is_safe_bundle_member('a/../b') is False
    assert _is_safe_bundle_member('a\\b') is False
    assert _is_safe_bundle_member('') is False
    assert _is_safe_bundle_member('a//b') is False
    assert _is_safe_bundle_member('./index.html') is False


# ---------- 集成（真实 PG + 真实 S3） ----------


async def _seed_owner() -> str:
    owner = f'h_publish_unpack_{uuid.uuid4().hex[:10]}'
    async with async_db_session.begin() as db:
        await db.execute(
            text(
                """
                INSERT INTO hasn_humans
                    (hasn_id, star_id, user_id, nickname, status, contact_policy, stats, created_time)
                VALUES
                    (:owner, :star, :user_id, :nickname, 'active', '{}'::jsonb, '{}'::jsonb, now())
                """
            ),
            {
                'owner': owner,
                'star': f'mg{uuid.uuid4().int % 9_000_000 + 1_000_000}',
                'user_id': uuid.uuid4().int % 9_000_000 + 1_000_000,
                # nickname 有唯一索引（idx_hasn_humans_nickname_unique），同测试连建两个 owner 会撞名
                'nickname': f'发布解包测试_{owner[-6:]}',
            },
        )
    return owner


async def _upload_zip_asset(owner: str, zip_bytes: bytes) -> tuple[str, str, list[str]]:
    """上传 zip 到私桶并登记 hasn_assets，返回 (asset_id, zip_object_key, 待清理 object_keys)。"""
    return await _upload_asset(owner, zip_bytes, 'bundle.zip', 'application/zip', kind='publish')


async def _upload_asset(
    owner: str, data: bytes, filename: str, mime: str, *, kind: str
) -> tuple[str, str, list[str]]:
    """上传任意字节到私桶并登记 hasn_assets，返回 (asset_id, object_key, 待清理 object_keys)。"""
    object_key = f'owners/{owner}/publish-test/{uuid.uuid4().hex[:12]}/{filename}'
    async with async_db_session.begin() as db:
        storage = await storage_service.get_storage(db, _PRIVATE_STORAGE_ID)
        await write_bytes(storage, object_key, data, mime)
        asset = await hasn_asset_service.register_asset(
            db,
            owner_hasn_id=owner,
            ref=ObjectRef(
                storage_id=_PRIVATE_STORAGE_ID,
                object_key=object_key,
                access='private',
                stable_url='',
                mime=mime,
                size=len(data),
            ),
            kind=kind,
            extract_status='done',
        )
        return asset.asset_id, object_key, [object_key]


async def _cleanup(owner: str, asset_id: str, object_keys: list[str]) -> None:
    async with async_db_session.begin() as db:
        storage = await storage_service.get_storage(db, _PRIVATE_STORAGE_ID)
        for key in object_keys:
            try:
                from backend.plugin.s3.utils.file_ops import delete_object

                await delete_object(storage, key)
            except Exception:  # noqa: BLE001 —— 清理尽力而为，不掩盖测试断言
                pass
        await db.execute(text('DELETE FROM hasn_publish.revision WHERE owner_id = :owner'), {'owner': owner})
        await db.execute(text('DELETE FROM hasn_publish.site WHERE owner_id = :owner'), {'owner': owner})
        await db.execute(text('DELETE FROM hasn_assets WHERE asset_id = :aid'), {'aid': asset_id})
        await db.execute(text('DELETE FROM hasn_humans WHERE hasn_id = :owner'), {'owner': owner})


@pytest.mark.asyncio
async def test_unpack_bundle_zip_writes_children_and_files_manifest() -> None:
    owner = await _seed_owner()
    zip_bytes = _make_zip(
        {
            'index.html': b'<html><body>deck</body></html>',
            'assets/a.png': b'\x89PNG-fake-a',
            'assets/b.png': b'\x89PNG-fake-b',
        }
    )
    asset_id, zip_key, keys = await _upload_zip_asset(owner, zip_bytes)
    written: list[str] = []
    try:
        async with async_db_session.begin() as db:
            files = await _unpack_bundle_zip_files(db, owner_id=owner, asset_id=asset_id)

        assert set(files) == {'index.html', 'assets/a.png', 'assets/b.png'}
        entry = files['index.html']
        assert entry['mime'] == 'text/html'
        assert entry['storage_id'] == _PRIVATE_STORAGE_ID
        assert entry['object_key'].startswith(f'owners/{owner}/publish/{asset_id}/')
        written = [f['object_key'] for f in files.values()]

        # 子对象真实可读回、字节一致（不是只写了空壳引用）
        async with async_db_session.begin() as db:
            data = await storage_service.read_bytes(
                db, storage_id=_PRIVATE_STORAGE_ID, object_key=entry['object_key']
            )
        assert data == b'<html><body>deck</body></html>'
        assert files['assets/a.png']['mime'] == 'image/png'
        assert files['assets/a.png']['size'] == len(b'\x89PNG-fake-a')
    finally:
        await _cleanup(owner, asset_id, [*keys, *written])


@pytest.mark.asyncio
async def test_materialize_referenced_assets_copies_server_side() -> None:
    """referenced 资产经 create_site 物化：同桶 copy 落 files，字节一致且原子对象仍在（copy ≠ move）。"""
    owner = await _seed_owner()
    # 原图：直接在私桶造一份「deck 页引用的图」（本就在桶里，不走 zip）
    image_bytes = b'\x89PNG-deck-image'
    img_id, img_key, img_keys = await _upload_asset(owner, image_bytes, 'deck-img.png', 'image/png', kind='deck')
    # 发布制品：纯 HTML（引用模式下图片不进制品）
    html_bytes = b'<html><body><img src="assets/pic"></body></html>'
    pub_id, pub_key, pub_keys = await _upload_asset(owner, html_bytes, 'index.html', 'text/html', kind='publish')
    written: list[str] = []
    try:
        manifest_in = {
            'runtime': 'single-html',
            'format': 'single-html',
            'entry': 'index.html',
            'assets': [
                {
                    'source': f'hasn://asset/{img_id}',
                    'name': 'assets/pic',
                    'content_type': 'image/png',
                    'size': None,
                    'status': 'referenced',
                    'asset_id': img_id,
                }
            ],
        }
        async with async_db_session.begin() as db:
            result = await publish_service.create_site(
                db,
                owner_id=owner,
                publisher_agent_id=None,
                kind='deck',
                title='引用物化测试',
                asset_id=pub_id,
                runtime='single-html',
                manifest_json=manifest_in,
                visibility='public',
            )
        manifest = result['revision']['manifest_json']
        files = manifest.get('files')
        assert isinstance(files, dict) and 'assets/pic' in files, f'manifest.files 缺引用资产项: {manifest!r}'
        entry = files['assets/pic']
        assert entry['mime'] == 'image/png'
        assert entry['size'] == len(image_bytes)
        assert entry['object_key'] == f'owners/{owner}/publish/{pub_id}/assets/pic'
        # 打包侧溯源键保留（assets[] 原样留在 manifest 里）
        assert manifest['assets'][0]['status'] == 'referenced'
        written.append(entry['object_key'])

        async with async_db_session.begin() as db:
            copied = await storage_service.read_bytes(
                db, storage_id=_PRIVATE_STORAGE_ID, object_key=entry['object_key']
            )
            original = await storage_service.read_bytes(db, storage_id=_PRIVATE_STORAGE_ID, object_key=img_key)
        assert copied == image_bytes, 'copy 后字节必须与原图一致'
        assert original == image_bytes, '原图必须仍在（copy 不是 move，主人资产不受发布影响）'
    finally:
        await _cleanup(owner, img_id, img_keys)
        await _cleanup(owner, pub_id, [*pub_keys, *written])


@pytest.mark.asyncio
async def test_materialize_referenced_assets_rejects_foreign_asset() -> None:
    """ACL：referenced 资产不属于发布者 → 显式报错（防把别人私图挂上自己的公开站点）。"""
    owner = await _seed_owner()
    other = await _seed_owner()
    foreign_id, foreign_key, foreign_keys = await _upload_asset(
        other, b'\x89PNG-secret', 'secret.png', 'image/png', kind='deck'
    )
    try:
        with pytest.raises(errors.ServerError):
            async with async_db_session.begin() as db:
                await _materialize_referenced_assets(
                    db,
                    owner_id=owner,
                    publish_asset_id='pub_fake',
                    manifest={
                        'assets': [
                            {
                                'source': f'hasn://asset/{foreign_id}',
                                'name': 'assets/x',
                                'status': 'referenced',
                                'asset_id': foreign_id,
                            }
                        ]
                    },
                )
    finally:
        await _cleanup(other, foreign_id, foreign_keys)
        await _cleanup(owner, '', [])


@pytest.mark.asyncio
async def test_unpack_bundle_zip_rejects_bad_zip_and_missing_entry() -> None:
    owner = await _seed_owner()

    # 非 zip 字节 → ServerError（零 fake，不静默产出残缺 manifest）
    bad_id, bad_key, keys = await _upload_zip_asset(owner, b'not-a-zip-at-all')
    try:
        with pytest.raises(errors.ServerError):
            async with async_db_session.begin() as db:
                await _unpack_bundle_zip_files(db, owner_id=owner, asset_id=bad_id)
    finally:
        await _cleanup(owner, bad_id, keys)

    # 缺 index.html 入口 → ServerError
    no_entry = _make_zip({'assets/a.png': b'\x89PNG-x'})
    ne_id, ne_key, keys = await _upload_zip_asset(owner, no_entry)
    written: list[str] = []
    try:
        with pytest.raises(errors.ServerError):
            async with async_db_session.begin() as db:
                await _unpack_bundle_zip_files(db, owner_id=owner, asset_id=ne_id)
    finally:
        # 缺入口前已写的子对象（assets/a.png）也要清掉
        written.append(f'owners/{owner}/publish/{ne_id}/assets/a.png')
        await _cleanup(owner, ne_id, [*keys, *written])


@pytest.mark.asyncio
async def test_create_site_bundle_zip_lands_files_manifest() -> None:
    """create_site 接入点：runtime='bundle-zip' 时 revision.manifest_json 落 files 格式（回归 410 根因）。"""
    owner = await _seed_owner()
    zip_bytes = _make_zip({'index.html': b'<html><body>deck-v1</body></html>', 'assets/a.png': b'\x89PNG-a'})
    asset_id, zip_key, keys = await _upload_zip_asset(owner, zip_bytes)
    written: list[str] = []
    asset2_id = ''
    try:
        async with async_db_session.begin() as db:
            result = await publish_service.create_site(
                db,
                owner_id=owner,
                publisher_agent_id=None,
                kind='deck',
                title='解包接入测试',
                asset_id=asset_id,
                runtime='bundle-zip',
                visibility='public',
            )
        manifest = result['revision']['manifest_json']
        assert isinstance(manifest, dict) and isinstance(manifest.get('files'), dict), (
            f'manifest_json 应为 files 格式，实际: {manifest!r}'
        )
        assert 'index.html' in manifest['files']
        written = [f['object_key'] for f in manifest['files'].values()]

        # update 接入点：新 revision 同样落 files 格式
        asset2_id, zip2_key, _ = await _upload_zip_asset(
            owner, _make_zip({'index.html': b'<html><body>deck-v2</body></html>'})
        )
        keys.append(zip2_key)
        async with async_db_session.begin() as db:
            updated = await publish_service.update_site(
                db,
                owner_id=owner,
                site_id=result['site']['id'],
                asset_id=asset2_id,
                runtime='bundle-zip',
            )
        manifest2 = updated['revision']['manifest_json']
        assert isinstance(manifest2, dict) and 'index.html' in manifest2.get('files', {})
        written += [f['object_key'] for f in manifest2['files'].values()]
    finally:
        if asset2_id:
            await _cleanup(owner, asset2_id, [])
        await _cleanup(owner, asset_id, [*keys, *written])
