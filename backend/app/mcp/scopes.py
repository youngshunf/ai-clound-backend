"""Scope 展示元数据聚合器（catalog 中文化 / 分组 / 风险展示）。

设计事实源：16-工具授权统一与权限声明 Manifest 化 D-v3-3。
- **判定真相**仍是各 `BaseTool.required_scopes` + 三态 mode；本模块只负责**展示元数据**
  （中文 label / domain / risk / 描述），通过 scope key 关联。risk 仅 UI 提示（不强制确认，D4）。
- **声明源按应用组织**（不再一个大文件混所有应用域）：platform 域在 `platform_scopes.py`；
  app 域随各应用目录的 `scopes.py`（deck/hasn_community/hasn_task/publish）+ knowledge 声明在
  其 manifest 模块。本文件只把它们**聚合**成统一查询表，新增/删除应用只动该应用目录、不动大文件。
- catalog 渲染缺失元数据时回退到 scope key 本身（不崩、不造假）。
"""

from __future__ import annotations

from typing import Any

from backend.app.hasn_community.scopes import COMMUNITY_SCOPE_CATALOG
from backend.app.hasn_computer_use.scopes import COMPUTER_USE_SCOPE_CATALOG
from backend.app.hasn_core.app_platform import KNOWLEDGE_SCOPE_CATALOG
from backend.app.hasn_creator.scopes import CREATOR_SCOPE_CATALOG
from backend.app.hasn_deck.scopes import DECK_SCOPE_CATALOG
from backend.app.hasn_design.scopes import DESIGN_SCOPE_CATALOG
from backend.app.hasn_designsystem.scopes import DESIGNSYSTEM_SCOPE_CATALOG
from backend.app.hasn_diag.scopes import DIAG_SCOPE_CATALOG
from backend.app.hasn_film.scopes import FILM_SCOPE_CATALOG
from backend.app.hasn_finance.scopes import FINANCE_SCOPE_CATALOG
from backend.app.hasn_growth.scopes import HASN_GROWTH_SCOPE_CATALOG
from backend.app.hasn_imagelab.scopes import IMAGELAB_SCOPE_CATALOG
from backend.app.hasn_plan.scopes import PLAN_SCOPE_CATALOG
from backend.app.hasn_project.scopes import PROJECT_SCOPE_CATALOG
from backend.app.hasn_publish.scopes import PUBLISH_SCOPE_CATALOG
from backend.app.hasn_quant.scopes import QUANT_SCOPE_CATALOG
from backend.app.hasn_reel.scopes import REEL_SCOPE_CATALOG
from backend.app.hasn_studio.scopes import STUDIO_SCOPE_CATALOG
from backend.app.hasn_task.scopes import HASN_TASK_SCOPE_CATALOG
from backend.app.mcp.platform_scopes import PLATFORM_SCOPE_CATALOG

# 聚合视图：platform（平台域）∪ app（各应用目录声明）。
# scope_key 在各源不重叠（platform vs app 互斥）；如有重叠，后者覆盖前者（app 优先于历史兜底）。
SCOPE_CATALOG: dict[str, dict[str, str]] = {
    **PLATFORM_SCOPE_CATALOG,
    **DECK_SCOPE_CATALOG,
    **DESIGNSYSTEM_SCOPE_CATALOG,
    **COMMUNITY_SCOPE_CATALOG,
    **COMPUTER_USE_SCOPE_CATALOG,
    **KNOWLEDGE_SCOPE_CATALOG,
    **HASN_TASK_SCOPE_CATALOG,
    **HASN_GROWTH_SCOPE_CATALOG,
    **CREATOR_SCOPE_CATALOG,
    **PUBLISH_SCOPE_CATALOG,
    **FILM_SCOPE_CATALOG,
    **REEL_SCOPE_CATALOG,
    **IMAGELAB_SCOPE_CATALOG,
    **DIAG_SCOPE_CATALOG,
    **PLAN_SCOPE_CATALOG,
    **PROJECT_SCOPE_CATALOG,
    **FINANCE_SCOPE_CATALOG,
    **QUANT_SCOPE_CATALOG,
    **STUDIO_SCOPE_CATALOG,
    **DESIGN_SCOPE_CATALOG,
}

# source 分组的中文/英文标签（catalog 顶层分组，i18n 双语）。
# 前端按当前 locale 取 zh/en，不再自维护文案。
SOURCE_LABELS: dict[str, dict[str, str]] = {
    'platform': {'zh': '平台工具', 'en': 'Platform Tools'},
    'app': {'zh': 'AI-Native 应用', 'en': 'AI-Native Apps'},
    'external': {'zh': '外部 MCP', 'en': 'External MCP'},
    # daemon 并入的本地引擎工具分组（云端 catalog 不产 local，此处供口径统一/前端回退）。
    'local': {'zh': '本地工具', 'en': 'Local Tools'},
}

# domain（能力域）分组的中文/英文名（catalog 二级分组标题，i18n 双语）。
# ⭐ 单一事实源：前端只保留 domain→图标 映射，分组显示名一律取这里、跟随语言设置，
# 不再由前端手维护中文名（曾因漏映射而露英文键）。新增 domain 必须在此登记。
DOMAIN_LABELS: dict[str, dict[str, str]] = {
    # —— 平台工具域 ——
    'asset': {'zh': '媒体资产', 'en': 'Assets'},
    'contact': {'zh': '联系人', 'en': 'Contacts'},
    'message': {'zh': '消息', 'en': 'Messages'},
    'user': {'zh': '账户', 'en': 'Account'},
    'profile': {'zh': '资料', 'en': 'Profile'},
    'marketplace': {'zh': '能力市场', 'en': 'Marketplace'},
    'media': {'zh': '媒体生成', 'en': 'Media'},
    'video': {'zh': '视频生成', 'en': 'Video'},
    'memory': {'zh': '记忆', 'en': 'Memory'},
    'session': {'zh': '会话', 'en': 'Session'},
    'workbench': {'zh': '工作台', 'en': 'Workbench'},
    'diag': {'zh': '平台诊断', 'en': 'Diagnostics'},
    # —— 应用域 ——
    'plan': {'zh': '规划', 'en': 'Planning'},
    'project': {'zh': '项目', 'en': 'Projects'},
    'task': {'zh': '任务与工作流', 'en': 'Tasks & Workflows'},
    'deck': {'zh': '演示文稿', 'en': 'Slides'},
    'designsystem': {'zh': '设计系统', 'en': 'Design System'},
    'community': {'zh': '社区', 'en': 'Community'},
    'knowledge': {'zh': '知识库', 'en': 'Knowledge'},
    'growth': {'zh': '智能获客', 'en': 'Growth'},
    'creator': {'zh': '内容创作', 'en': 'Content Studio'},
    'publish': {'zh': '网页发布', 'en': 'Web Publishing'},
    'finance': {'zh': '金融行情', 'en': 'Markets'},
    'quant': {'zh': '量化策略', 'en': 'Quant'},
    'studio': {'zh': '视频工坊', 'en': 'Video Studio'},
    'imagelab': {'zh': '图坊', 'en': 'Image Lab'},
    'computer_use': {'zh': '桌面控制', 'en': 'Computer Use'},
    # —— 本地引擎工具域 ——
    'film': {'zh': '影视创作', 'en': 'Film'},
    'reel': {'zh': '短视频', 'en': 'Short Video'},
    'design': {'zh': '矢量设计', 'en': 'Vector Design'},
}

# 权限页「展示分组」重分类白名单：以下 domain 虽注册为 platform 工具（TOOLMIG 把 deck/
# designsystem 工具迁成云端 platform 工具），但语义是**独立 AI-Native 应用**（有独立 app
# catalog + 工作台页）→ 权限页归「AI-Native 应用」来源组，不混进平台底座。此重分类**仅影响
# catalog 展示分组**，不改工具 source（tool.search / 执行路由不受影响）。
# task/workflow/plan/marketplace/memory/message/asset/contact 是每个分身通用的平台底座能力，
# 故**不在**此集合（留「平台工具」组，福仔 2026-07-03 明确）。
APP_DISPLAY_DOMAINS: frozenset[str] = frozenset({'deck', 'designsystem'})


def domain_label(domain: str, lang: str = 'zh') -> str:
    """取 domain 的中文/英文分组名；未登记则回退 domain 键本身（不造假、暴露缺失）。"""
    entry = DOMAIN_LABELS.get(domain)
    if entry:
        return entry.get(lang) or entry.get('zh') or domain
    return domain


def scope_meta(scope_key: str) -> dict[str, Any]:
    """取 scope 展示元数据（i18n 双语）；缺失则回退到 key 本身（不造假）。

    - `label`/`description` 主字段=中文（向后兼容既有消费方 ask_gate / 授予页）；
      `label_en`/`description_en` 为英文，**英文缺失时诚实回退中文**（宁可显示中文，
      也不露 scope key、不造假）。
    - `domain_label`/`domain_label_en` = 该能力域的中文/英文分组名（取自 `DOMAIN_LABELS`），
      供前端按当前语言渲染分组标题，替代前端手维护的域名映射（图标仍前端配）。
    """
    meta = SCOPE_CATALOG.get(scope_key)
    if meta:
        domain = meta.get('domain', '')
        label_zh = meta['label_zh']
        desc_zh = meta.get('description', '')
        return {
            'label': label_zh,
            'label_en': meta.get('label_en') or label_zh,
            'domain': domain,
            'domain_label': domain_label(domain, 'zh'),
            'domain_label_en': domain_label(domain, 'en'),
            'risk': meta.get('risk', 'low'),
            # 出厂默认三态：声明源未标注则回退 allow（与本地 resolver 缺省一致）。
            'default_mode': meta.get('default_mode', 'allow'),
            'description': desc_zh,
            'description_en': meta.get('description_en') or desc_zh,
        }
    domain = scope_key.split(':', 1)[0] if ':' in scope_key else ''
    return {
        'label': scope_key,
        'label_en': scope_key,
        'domain': domain,
        'domain_label': domain_label(domain, 'zh'),
        'domain_label_en': domain_label(domain, 'en'),
        'risk': 'low',
        'default_mode': 'allow',
        'description': '',
        'description_en': '',
    }
