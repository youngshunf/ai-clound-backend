"""旧稳定 URL 签名入口的资产 ACL 单元测试。"""

from __future__ import annotations

import pytest

from backend.app.hasn.model import HasnAssets
from backend.app.hasn.service.hasn_asset_service import HasnAssetService
from backend.common.exception import errors


def _asset(*, owner_hasn_id: str, access: str) -> HasnAssets:
    return HasnAssets(
        asset_id='ast_acl_test',
        owner_hasn_id=owner_hasn_id,
        access=access,
        storage_id=1,
        object_key='owners/scope/objects/object-id',
        kind='file',
        mime='application/octet-stream',
        size_bytes=1,
    )


def test_private_asset_owner_can_request_legacy_signed_url() -> None:
    asset = _asset(owner_hasn_id='h_owner', access='private')

    HasnAssetService.assert_legacy_sign_allowed(asset=asset, requester_hasn_id='h_owner')


def test_private_asset_rejects_other_owner() -> None:
    asset = _asset(owner_hasn_id='h_owner', access='private')

    with pytest.raises(errors.ForbiddenError):
        HasnAssetService.assert_legacy_sign_allowed(asset=asset, requester_hasn_id='h_other')


def test_public_asset_allows_authenticated_reader() -> None:
    asset = _asset(owner_hasn_id='h_owner', access='public')

    HasnAssetService.assert_legacy_sign_allowed(asset=asset, requester_hasn_id='h_other')
