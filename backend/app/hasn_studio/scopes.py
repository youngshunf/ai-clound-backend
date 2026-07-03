"""studio（OpenMontage 统一视频引擎接入应用，模块 14 doc22）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/22-OpenMontage统一视频引擎(云服务·工具即服务)选型设计.md §3；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

三态出厂默认（doc22，贴「出厂默认是唯一真相」+「花算力/外发出厂即 ask」）：
- 查看项目/素材/成品/渲染任务（read）= **allow**（只读、廉价，分身随便看）；
- 保存/更新项目/分镜（write）= **allow**（不出片、不花算力，分身随便迭代）；
- 提交渲染 / run_pipeline（render）= **ask**（花算力出片，走审批卡，主人裁决）；
- 导出成片（export）= **ask**（花算力/外发，主人裁决）；
- 分享/发布（share）= **ask**（外发，主人裁决）。

⚠️ cloud-brokered：工具注册在**云端 MCP**（对齐 creator/finance/quant），非本地工具。
本表是云端侧契约源；daemon `crates/.../studio` 侧（待落）须与 required_scopes 对齐（跨仓零漂移守卫）。

媒体 provider 凭据管理（key 增删）= **owner 经 webui** 的动作，无对应 MCP 写工具，不进本 scope 表。
"""

from __future__ import annotations

STUDIO_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'studio:read': {
        'label_zh': '查看视频项目与成品',
        'label_en': 'View video projects and outputs',
        'domain': 'studio',
        'risk': 'low',
        'description': '以 Agent 身份读取视频项目、素材库、成品、渲染任务（只读，owner 隔离）',
        'description_en': 'Read video projects, material libraries, finished outputs, and render jobs as the agent (read-only, owner-isolated)',
    },
    'studio:write': {
        'label_zh': '保存/更新视频项目',
        'label_en': 'Save/update video projects',
        'domain': 'studio',
        'risk': 'low',
        'description': '保存/更新视频项目、分镜与项目设置（不出片、不花算力，分身可随便迭代）',
        'description_en': 'Save and update video projects, storyboards, and project settings (no rendering, no compute; the agent can iterate freely)',
    },
    'studio:render': {
        'label_zh': '提交渲染/出片',
        'label_en': 'Submit render / produce',
        'domain': 'studio',
        'risk': 'medium',
        'default_mode': 'ask',
        'description': '提交渲染 / run_pipeline（脚本→分镜→配音→合成出片，花算力，须主人审批）',
        'description_en': 'Submit a render / run_pipeline (script→storyboard→voiceover→composition, compute-heavy, requires owner approval)',
    },
    'studio:export': {
        'label_zh': '导出成片',
        'label_en': 'Export final cut',
        'domain': 'studio',
        'risk': 'medium',
        'default_mode': 'ask',
        'description': '导出最终成片（花算力/外发，须主人审批）',
        'description_en': 'Export the final cut (compute-heavy, outbound, requires owner approval)',
    },
    'studio:share': {
        'label_zh': '分享/发布成片',
        'label_en': 'Share/publish output',
        'domain': 'studio',
        'risk': 'low',
        'default_mode': 'ask',
        'description': '分享或对外发布成片（外发，换签名 URL，须主人审批）',
        'description_en': 'Share or publish the finished output (outbound, swapped for a signed URL, requires owner approval)',
    },
}
