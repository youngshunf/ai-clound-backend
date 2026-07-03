"""design（OpenPencil 矢量设计工具接入应用，模块 14 doc27）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/27-OpenPencil矢量设计工具接入设计(本地sidecar·画布即应用).md
（§5.3/§5.4）；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（hasn-node `crates/hasn-mcp/src/design.rs`，OP-P3-A 待落；本表是云端侧契约源，
design.rs `capability_scopes()` 须与之对齐——同 reel/film 的跨仓零漂移守卫）：
- 读类工具（get/get_selection/read_nodes/find_empty_space/get_design_prompt/export）统一 `design:read`
  （出厂 Allow——读画布/取设计知识/导出渲染结果，分身随便看，§5.3 表 export=design:read）；
- 写类工具（batch_design/skeleton/content/refine/节点增改/set_variables/set_themes）统一 `design:write`
  （创作类出厂 **Allow**——画布迭代不出片不花算力，分身可随便画；破坏性 delete_node/replace_node 仍落
  `design:write` 但 capability `human_confirmation=True` 出厂 **Ask**，§5.3 note「读类/创作类 Allow + 破坏性 Ask」）；
- 代码生成（codegen plan/submit/assemble）落 `design:codegen`（出厂 Allow，确定性出码）。

注：design 是**本地 sidecar 工具**（execution_mode=local_tool，工具在本地 hasn-mcp 注册，云端 tools[] 置空），
分身经 daemon 三态闸门调用——故 scope 登记进 platform_scopes 展示词表（同 reel/film，区别于 cloud-brokered 的 studio）。授权走三态 capability_modes，JWT scopes 已退役（实施102 S0）。
"""

from __future__ import annotations

DESIGN_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'design:read': {
        'label_zh': '查看设计画布',
        'domain': 'design',
        'risk': 'low',
        'description': '以 Agent 身份读取设计画布、当前选中、设计知识 prompt，并导出渲染结果（截图/SVG，owner 隔离）',
    },
    'design:write': {
        'label_zh': '在画布上出设计',
        'domain': 'design',
        # 创作类出厂 allow（画布迭代不花算力、不出片，分身可随便画）；破坏性 delete/replace 走
        # capability human_confirmation=True 出厂 ask（对齐 studio:write 哲学 + §5.3 note）。
        'default_mode': 'allow',
        'risk': 'low',
        'description': '在画布上出/改设计：一次成型或分层、节点增改、变量主题（创作类放行，删/替换需确认）',
    },
    'design:codegen': {
        'label_zh': '设计稿出代码',
        'domain': 'design',
        'default_mode': 'allow',
        'risk': 'low',
        'description': '把设计稿生成多平台代码（plan→submit→assemble，确定性出码，owner 隔离）',
    },
}
