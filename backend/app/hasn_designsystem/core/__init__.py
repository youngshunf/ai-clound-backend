"""``hasn_designsystem.core`` —— 设计系统 token 契约确定性引擎（离线纯函数地基）。

Python 移植 hasn-node `hasn-designsystem-core`（Rust）。把「生成一套设计系统」里**确定性的部分**
（四层 token 契约 + 标准命名 + 派生 + 评分 + 组件提取）落成纯函数，供云端 platform MCP 工具直接调用——
云端分身（Hermes Runtime）经 ``/api/v1/mcp/streamable`` 即可用（补齐原本只在本地 hasn-mcp 的能力）。

TOOLMIG（2026-07-04·福仔「直接用云端 python 重写一遍，rust 侧逐渐退役」）：单一实现源收敛到 Python。

# 四把「刀」
- :func:`compile_tokens`：source token（导入/原始变量）→ 标准命名 + 四层归类 + 缺槽别名回填的契约（含渲染 tokens.css）。
- :func:`derive`：tokens.css → design-tokens.json（``hasn-design-tokens/v1``）+ tailwind-v4.css。
- :func:`validate`：tokens.css（+ 可选 components.html）→ 四层契约校验
  + 评分（0–100 / grade / recommendRebuild / issues）。
- :func:`extract_components`：components.html（+ 可选 tokens.css）→ components.manifest.json。

# 纯函数约定
所有时间戳由调用方以 ISO 字符串传入（``generated_at``），不读时钟，保证可测、可重放。
"""

from __future__ import annotations

from .components import COMPONENTS_MANIFEST_SCHEMA_VERSION, extract_components
from .contract import (
    Binding,
    DesignSystemContract,
    SourceToken,
    binding_to_dict,
    compile_tokens,
    render_contract_css,
    validate,
    validate_token_outputs,
)
from .derive import (
    TAILWIND_V4_THEME_BINDINGS,
    derive,
    infer_design_token_type,
    render_design_tokens_json,
    render_tailwind_v4_css,
)
from .gallery_projection import slice_gallery_scene, summarize_gallery
from .schema import (
    TOKEN_SCHEMA,
    TokenSpec,
    all_schema_names,
    is_allowed_extension,
    spec_for,
)
from .scenes import (
    DEFAULT_REQUIRED_SCENES,
    SCENE_STANDARDS,
    SceneComponent,
    SceneStandard,
    detect_scenes,
    is_known_scene,
    known_scene_ids,
)

__all__ = [
    'COMPONENTS_MANIFEST_SCHEMA_VERSION',
    'DEFAULT_REQUIRED_SCENES',
    'SCENE_STANDARDS',
    'TAILWIND_V4_THEME_BINDINGS',
    'TOKEN_SCHEMA',
    'Binding',
    'DesignSystemContract',
    'SceneComponent',
    'SceneStandard',
    'SourceToken',
    'TokenSpec',
    'all_schema_names',
    'binding_to_dict',
    'compile_tokens',
    'derive',
    'detect_scenes',
    'extract_components',
    'infer_design_token_type',
    'is_allowed_extension',
    'is_known_scene',
    'known_scene_ids',
    'render_contract_css',
    'render_design_tokens_json',
    'render_tailwind_v4_css',
    'slice_gallery_scene',
    'spec_for',
    'summarize_gallery',
    'validate',
    'validate_token_outputs',
]
