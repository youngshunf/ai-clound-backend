"""响应信封契约守卫（项目硬规则）。

**规则**：fba 云端"正常业务接口"必须用统一返回格式——
`response_model=ResponseModel`（含子类 `ResponseSchemaModel`）+ `return response_base.success(...)`。
daemon 经 transport `.send()` → `decode_ok_envelope` 解析 `{code,msg,data}` 信封；
handler 若裸返回 Pydantic 模型会让 daemon 解析失败（2026-06-02 权限 tab `get_scope_catalog`
"error decoding response body" 即此，修复 commit 54da4c4）。

**裸返回仅限"统一信封根本满足不了"的接口**：OpenAI/Anthropic 兼容代理（外部 SDK 按原生形状解析）、
文件/YAML/下载/导出、重定向、第三方 webhook（须回 provider 指定文本）等。这些 + 当前历史欠债端点
冻在下方 `KNOWN_NON_ENVELOPE` 基线里。

本守卫只挡**新增漂移**：任何新路由若不返回 ResponseModel 且不在基线 → 失败。届时：
- 正常业务接口 → 改成 `response_model=ResponseModel` + `response_base.success(...)`；
- 确属真例外 → 显式加进基线 GENUINE 段并写明"信封哪满足不了"。
迁移欠债端点到信封后，记得从 DEBT 段删除对应行（`test_no_stale_baseline` 会提醒）。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from backend.common.response.response_schema import ResponseModel
from backend.plugin.core import build_final_router

# ---- 真例外（统一信封满足不了，永久保留）----
_GENUINE = {
    # OpenAI/Anthropic 兼容代理：外部 SDK 按原生形状解析，套信封即违反协议
    # （旧 /api/v1/llm/proxy/* 系列已随 app/llm 自建网关删除——NEWAPI-P6 new-api 解耦；
    #  /api/v1/hermes/app/agents/{agent_id}/chat/completions 已随 app/hermes 整模块删除——2026-08-10）
    # 文件 / YAML / 下载 / 导出（返回二进制或文本文件，非 JSON 业务体）
    'GET /api/v1/client/version/latest-linux.yml',
    'GET /api/v1/client/version/latest-mac.yml',
    'GET /api/v1/client/version/latest.yml',
    # 桌面端发布 hasn_release（Tauri 自动更新 + 下载）：
    #   - updater 返回 Tauri v2 原生 manifest JSON 形状（客户端按原生解析），且无更新时 204 无体；
    #   - download 302 重定向到七牛 CDN 直链（含下载计数副作用）。二者信封套不了。
    'GET /api/v1/release/open/updater/{target}/{arch}/{current_version}',
    'GET /api/v1/release/open/download/{asset_id}',
    'GET /api/v1/code-generation/generations/{pk}',
    'GET /api/v1/marketplace/open/skills/{resource_id:path}/download',
    'GET /api/v1/marketplace/open/templates/{resource_id:path}/download',
    # 个人技能库私有包带鉴权下载（SKILLSYNC-C2 难点5）：Agent JWT 校验 owner 归属后
    # StreamingResponse 直回 zip 字节（不 302 跳私有桶，因 hermes urllib 取私有技能会 401）
    'GET /api/v1/marketplace/agent/personal-skills/{personal_skill_id:path}/download',
    # 知识库原件下载（D10）：私有桶流式二进制透传（StreamingResponse），信封满足不了
    'GET /api/v1/knowledge/app/documents/{doc_id}/download',
    # 网页发布公开查看面 /s/*（模块 18，独立分享域名）：浏览器直接加载的公开 HTML 外壳 /
    # 二进制制品 / 解锁 JSON——制品内容绝不在 API 主域以信封套壳；/content 恒带 CSP sandbox（见 hosting.py）
    'GET /s/{slug}',
    'POST /s/{slug}/unlock',
    'GET /s/{slug}/content',
    'GET /s/{slug}/assets/{name:path}',
    # 同上，票落在路径段的那对（制品内相对引用 `assets/x` 只能这样带上票，见 hosting.py 的
    # _TICKET_PATH_PREFIX）：与上面两条是同一份内容/字节，仅票的承载位置不同
    'GET /s/{slug}/t/{vt}/content',
    'GET /s/{slug}/t/{vt}/assets/{name:path}',
    # 重定向 / 外部 OAuth 回调
    'GET /api/v1/oauth2/github/callback',
    'GET /api/v1/oauth2/google/callback',
    # 第三方支付 webhook（须回 provider 指定文本，如 "success"）
    'POST /api/v1/pay/open/contract-notify/{channel_id}',
    'POST /api/v1/pay/open/notify/{channel_id}',
    'POST /api/v1/pay/open/refund-notify/{channel_id}',
    # GitHub webhook 需要按 provider 约定直接返回接收结果，不能套业务信封
    'POST /api/v1/marketplace/webhook/github/skills',
    'POST /api/v1/marketplace/webhook/github/templates',
    # Swagger 文档授权（fba 内置，给 docs UI 用）
    'POST /api/v1/auth/login/swagger',
    # 插件原始响应（plugin 自定义返回）
    'GET /api/v1/sys/plugins/{plugin}',
}

# ---- 已知欠债（正常业务接口，当前裸返回但自洽工作；按规则应逐步迁到信封，迁完删行）----
_DEBT = {
    # hasn 登录 / 同步：daemon 侧用 .send_json() 配对，迁移须云端+daemon 两仓同步改
    'POST /api/v1/hasn/auth/phone/send_code',
    'POST /api/v1/hasn/auth/phone/verify',
    'POST /api/v1/hasn/auth/token/refresh',
    'POST /api/v1/hasn/memory/sync/pull',
    'POST /api/v1/hasn/onboarding/ensure',
    # 'POST /api/v1/hasn/runtime/report' 已随云端 Runtime 形态退役摘除（2026-08-10），
    # 由 test_retired_routes_stay_gone 钉死不得复活
    'POST /api/v1/hasn/sync/pull',
    'POST /api/v1/hasn/sync/push',
    # hasn_task 模块（从 app/hasn 拆出）任务定义同步：daemon 侧 .send_json() 配对，同上 hasn/sync 系列
    'POST /api/v1/hasn-task/app/sync/pull',
    'POST /api/v1/hasn-task/app/sync/push',
    # hasn 企业 / ragflow：业务 JSON 但 handler 无 response_model 注解
    # （旧 active-workspaces / workspace/apps 已随 workspace 拆除删除——应用平台 v3 P3）
    'GET /api/v1/hasn/enterprise/invite-codes',
    'GET /api/v1/hasn/enterprise/memberships',
    'GET /api/v1/hasn/enterprises',
    'GET /api/v1/huanxing/analytics',
    # marketplace open 浏览：daemon 侧用 .send_json() 配对
    'GET /api/v1/marketplace/open/categories',
    'GET /api/v1/marketplace/open/categories/{category_slug}/skills',
    'GET /api/v1/marketplace/open/categories/{category_slug}/templates',
    'GET /api/v1/marketplace/open/skills',
    'GET /api/v1/marketplace/open/skills/search',
    'GET /api/v1/marketplace/open/skills/{resource_id:path}',
    'GET /api/v1/marketplace/open/templates',
    'GET /api/v1/marketplace/open/templates/search',
    'GET /api/v1/marketplace/open/templates/{resource_id:path}',
    'GET /api/v1/marketplace/open/trending/skills',
    'GET /api/v1/marketplace/open/trending/templates',
    # marketplace admin 同步工具：返回 dict
    'DELETE /api/v1/marketplace/admin/sync/cache',
    'GET /api/v1/marketplace/admin/sync/cache/stats',
    'GET /api/v1/marketplace/admin/sync/status',
    'POST /api/v1/marketplace/admin/sync/clawhub',
    'POST /api/v1/marketplace/admin/sync/github',
    'POST /api/v1/marketplace/admin/sync/github/templates',
    'POST /api/v1/marketplace/admin/sync/retranslate',
}

KNOWN_NON_ENVELOPE = _GENUINE | _DEBT


def _current_non_envelope() -> set[str]:
    """内省装配后的全部路由，返回不走信封的 `METHOD path` 集合。"""
    router = build_final_router()
    found: set[str] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        model = route.response_model
        if isinstance(model, type) and issubclass(model, ResponseModel):
            continue
        for method in route.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            found.add(f'{method} {route.path}')
    return found


def test_no_new_non_envelope_routes() -> None:
    """新增的业务接口必须走统一信封；不在基线的裸返回路由 → 失败。"""
    new = _current_non_envelope() - KNOWN_NON_ENVELOPE
    assert not new, (
        '发现新的非信封路由，违反"业务接口必须统一返回格式"硬规则。\n'
        '正常业务接口请用 response_model=ResponseModel + return response_base.success(data=...)；\n'
        '确属真例外（OpenAI 兼容代理/文件/重定向/webhook）才加入 KNOWN_NON_ENVELOPE 并注明理由：\n'
        + '\n'.join(f'  - {k}' for k in sorted(new))
    )


def _all_routes() -> set[str]:
    """装配后的全部路由（`METHOD path`），不区分是否走信封。"""
    router = build_final_router()
    found: set[str] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            found.add(f'{method} {route.path}')
    return found


# 已退役、且**不得复活**的路由（删除时连同基线条目一并搬到这里，防有人顺手挂回去）。
_RETIRED_ROUTES = {
    # 云端 Runtime 形态退役（2026-08-10）：云端不再部署 Runtime，改为每订阅一个完整的无头
    # hasn-node，Runtime 脱敏摘要上报写入端点随之摘除（表与历史事件保留，只删写入口）。
    'POST /api/v1/hasn/runtime/report',
    # app/hermes 整模块删除（2026-08-10）：Runtime 编排 API 与 OpenAI 兼容代理一并退役。
    'POST /api/v1/hermes/app/agents/{agent_id}/chat/completions',
}


def test_retired_routes_stay_gone() -> None:
    """防回归：已退役路由不得再出现在装配后的路由表里。"""
    revived = _RETIRED_ROUTES & _all_routes()
    assert not revived, (
        '以下路由已被显式退役，不应再注册；若确实要恢复，先确认对应形态决策是否回滚，'
        '再从 _RETIRED_ROUTES 移除：\n' + '\n'.join(f'  - {k}' for k in sorted(revived))
    )


def test_no_stale_baseline() -> None:
    """基线里已不存在或已迁到信封的条目应删除，保持名单诚实（欠债迁移后提醒清理）。"""
    stale = KNOWN_NON_ENVELOPE - _current_non_envelope()
    assert not stale, (
        '以下基线条目已不再是非信封路由（可能已迁到信封或路由变更），请从 '
        'KNOWN_NON_ENVELOPE 删除：\n' + '\n'.join(f'  - {k}' for k in sorted(stale))
    )


# DB 会话名永远是依赖（Depends）而非 query 参数。若某 handler 把
# `CurrentSession`/`CurrentSessionTransaction` 这类携带 Depends 的类型别名
# 仅放在 `if TYPE_CHECKING:` 块里，叠加文件头的 `from __future__ import annotations`，
# FastAPI 运行期解析不到该前向引用 → 丢失内嵌的 Depends → `db` 参数退化成
# **必填 query 参数** → 写入端点 422 {loc:[query,db]}（2026-06-29 知识库新建文档、
# 创作 creator.py 28 路由皆此）。`db`/`session` 绝不可能是合法 query 名，故钉死该回归。
_SESSION_PARAM_NAMES = {'db', 'session'}


def test_db_session_never_leaks_as_query_param() -> None:
    """DB 会话依赖必须解析为 Depends；若泄漏成 query 参数 = 丢了 Depends（见上注释）。"""
    router = build_final_router()
    leaks: list[str] = []
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for param in route.dependant.query_params:
            if param.name in _SESSION_PARAM_NAMES:
                methods = ','.join(sorted(route.methods - {'HEAD', 'OPTIONS'}))
                leaks.append(f'{methods} {route.path} -> query "{param.name}"')
    assert not leaks, (
        'DB 会话依赖泄漏成了必填 query 参数（FastAPI 运行期解析不到 Depends）。\n'
        '根因：handler 用的 CurrentSession/CurrentSessionTransaction 仅在 '
        '`if TYPE_CHECKING:` 下 import，叠加 `from __future__ import annotations`，\n'
        '运行期前向引用解析失败 → 丢 Depends → db 退化为 query 参数 → 写入端点 422。\n'
        '修法：把这些携带 Depends 的类型别名（及用于参数注解的 *Param/AgentTokenPayload 等）'
        '改为运行期顶层 import。受影响路由：\n'
        + '\n'.join(f'  - {k}' for k in sorted(leaks))
    )
