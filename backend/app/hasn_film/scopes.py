"""film（VideoClaw 视频生成应用，模块 14）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/18-VideoClaw视频生成应用接入与按需下载形态设计.md §7；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据。

落地真相（hasn-node `crates/hasn-mcp/src/film.rs`，VC-P4 已落）：
- 读类工具（project.list/get、stage.artifact）统一 `film:read`（出厂 Allow，只读）；
- 写类工具（project.create/各阶段生成/sandbox/pipeline/stage.intervene/continue）统一 `film:write`
  （出厂 Ask——视频/参考图花钱，owner 三态可覆盖）；
- 导出/上传类（artifact.upload，把本地产物显式传云端私有桶）统一 `film:export`（出厂 Ask）。
"""

from __future__ import annotations

FILM_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'film:read': {
        'label_zh': '查看视频项目',
        'label_en': 'View video projects',
        'domain': 'film',
        'risk': 'low',
        'description': '以 Agent 身份读取主人的视频项目、阶段产物与生成状态（只读，owner 隔离）',
        'description_en': "Read the owner's video projects, stage artifacts, and generation status as the agent (read-only, owner-isolated)",
    },
    'film:write': {
        'label_zh': '生成与编辑视频',
        'label_en': 'Generate and edit video',
        'domain': 'film',
        'risk': 'medium',
        'default_mode': 'allow',
        'description': '建项目、跑各阶段生成（剧本/角色/分镜/参考图/片段/后期）与短流程（生成类，2026-07-05 放开出厂 Allow，主人可改 Ask）',
        'description_en': 'Create projects and run stage generation (script/characters/storyboard/reference images/clips/post) and short pipelines (consumes owner quota; confirmation required by default)',
    },
    'film:export': {
        'label_zh': '上传/分享视频产物',
        'label_en': 'Upload/share video artifacts',
        'domain': 'film',
        'risk': 'medium',
        'default_mode': 'ask',
        'description': '把本地视频产物显式上传到云端私有桶以分享/跨设备查看（本地权威，默认需主人确认）',
        'description_en': 'Explicitly upload local video artifacts to a cloud private bucket for sharing or cross-device viewing (local-authoritative; owner confirmation required by default)',
    },
}
