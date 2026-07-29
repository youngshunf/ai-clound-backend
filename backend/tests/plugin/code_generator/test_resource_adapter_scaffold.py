"""G6 S3-5 codegen 收编守卫：新 app 干跑生成物含 resource_adapter 骨架（doc33 S3-5）。

断言 codegen 应用模板把 `<app>/service/resource_adapter.py` 纳入生成物路径映射（= 新 app 干跑即产出
G6 资源适配器骨架），且骨架模板渲染出**合法 Python**，含四要素（registry 注册 / ResourceMeta / 别名 /
load_meta）。生成器写盘「文件已存在则跳过」（`gen_service` §461），故对既有已手写 adapter 的应用绝不覆盖——
本守卫只保证「新 app 有骨架」，不改动既有 adapter。
"""

from __future__ import annotations

import types

from typing import Any

import pytest

from backend.plugin.code_generator.utils.gen_template import GenTemplate

_ADAPTER_TPL = 'python/resource_adapter.jinja'


def _stub_business(app_name: str = 'hasn_demo') -> Any:
    """构造一个仅够 get_template_path_mapping / _parse_scopes 求值的最小 business 桩。"""
    return types.SimpleNamespace(app_name=app_name, filename='demo', api_version='v1', api_scope='app')


def test_mapping_includes_resource_adapter_skeleton() -> None:
    """新 app 生成物映射含 <app>/service/resource_adapter.py（= 干跑即产出 G6 适配器骨架）。"""
    mapping = GenTemplate.get_template_path_mapping(_stub_business('hasn_demo'))
    assert mapping.get(_ADAPTER_TPL) == 'hasn_demo/service/resource_adapter.py'


@pytest.mark.asyncio
async def test_resource_adapter_template_renders_valid_skeleton() -> None:
    """骨架模板渲染出合法 Python，含四要素（注册表 / ResourceMeta / 别名 / load_meta）。"""
    code = await (
        GenTemplate()
        .get_template(_ADAPTER_TPL)
        .render_async(
            app_name='hasn_demo', class_name='Demo', table_name='hasn_demo', filename='demo', table_comment='演示'
        )
    )
    compile(code, '<resource_adapter>', 'exec')  # 断言渲染出的是合法 Python（骨架不可产出语法错误）
    for marker in ('DemoResourceAdapter', 'resource_kind_registry', 'ResourceMeta', 'id_param_aliases', 'load_meta'):
        assert marker in code, f'骨架缺要素: {marker}'
