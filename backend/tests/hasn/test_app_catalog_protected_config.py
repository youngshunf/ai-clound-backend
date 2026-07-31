"""通用「编辑配置」面不得改写签名发布端点独占的配置子树。

`PUT /{pk}/config` 是整块覆盖式写 config_json，与签名发布端点共用同一
`hasn:app:catalog:edit` 权限且互不感知：运营改一个无关字段保存，就会静默删掉全网
在用的签名目录 / 引擎清单，接口仍返回 200，daemon 重拉后模型集体失效。
"""

from __future__ import annotations

import pytest

from backend.app.hasn.service.hasn_app_catalog_service import _preserve_protected_config_subtrees
from backend.common.exception import errors

_SIGNED_CATALOG = {'payload': {'release_sequence': 2026073101}, 'signature': 'a' * 128}
_ENGINE = {'payload': {'schema_version': 2}, 'signature': 'b' * 128}


def test_saving_unrelated_fields_does_not_drop_the_signed_catalog() -> None:
    """整块覆盖的保存请求通常根本不带签名子树——必须回填，否则就是静默删除。"""
    current = {'models': {'signed_catalog': _SIGNED_CATALOG, 'matte_default': 'u2netp'}, 'engine': _ENGINE}
    merged = _preserve_protected_config_subtrees(current, {'models': {'matte_default': 'birefnet-general'}})
    assert merged['models']['signed_catalog'] == _SIGNED_CATALOG
    assert merged['engine'] == _ENGINE
    # 非受保护键仍以入参为准。
    assert merged['models']['matte_default'] == 'birefnet-general'


def test_saving_a_config_without_models_node_still_restores_signed_catalog() -> None:
    """连 models 节点都没带的保存请求同样不得抹掉签名目录。"""
    current = {'models': {'signed_catalog': _SIGNED_CATALOG}}
    merged = _preserve_protected_config_subtrees(current, {'unrelated': 1})
    assert merged['models']['signed_catalog'] == _SIGNED_CATALOG
    assert merged['unrelated'] == 1


def test_attempting_to_rewrite_the_signed_catalog_is_rejected() -> None:
    """签名子树只能由发布端点写入，通用配置面改动即 400。"""
    current = {'models': {'signed_catalog': _SIGNED_CATALOG}}
    tampered = {'models': {'signed_catalog': {'payload': {'release_sequence': 1}, 'signature': 'c' * 128}}}
    with pytest.raises(errors.RequestError, match=r'models\.signed_catalog'):
        _preserve_protected_config_subtrees(current, tampered)


def test_attempting_to_rewrite_the_engine_manifest_is_rejected() -> None:
    current = {'engine': _ENGINE}
    with pytest.raises(errors.RequestError, match='配置项 engine'):
        _preserve_protected_config_subtrees(current, {'engine': {'payload': {'schema_version': 9}}})


def test_identical_signed_subtree_passes_through() -> None:
    """原样回传（管理端读改写整份配置的常见形态）不算改动。"""
    current = {'models': {'signed_catalog': _SIGNED_CATALOG}}
    merged = _preserve_protected_config_subtrees(current, {'models': {'signed_catalog': _SIGNED_CATALOG}})
    assert merged['models']['signed_catalog'] == _SIGNED_CATALOG


def test_first_time_config_without_existing_signed_subtree_is_untouched() -> None:
    """库里还没有签名子树时不凭空造键。"""
    merged = _preserve_protected_config_subtrees(None, {'models': {'matte_default': 'u2netp'}})
    assert merged == {'models': {'matte_default': 'u2netp'}}


def test_incoming_payload_is_not_mutated_in_place() -> None:
    """入参来自请求体，回填不得就地改写调用方对象。"""
    current = {'models': {'signed_catalog': _SIGNED_CATALOG}}
    incoming = {'models': {'matte_default': 'u2netp'}}
    _preserve_protected_config_subtrees(current, incoming)
    assert incoming == {'models': {'matte_default': 'u2netp'}}
