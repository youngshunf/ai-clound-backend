"""computer_use（分身 GUI 桌面控制 · Computer Use，模块 23 V2）scope 展示元数据。

设计事实源：docs/hasn-node设计文档/23-分身桌面控制Computer-Use/02-分身GUI桌面控制接入设计V2-hasn-mcp统一接入与能力型应用.md §3.1/§3.3；
16-工具授权统一 D-v3-3（app 域 scope 元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。
判定真相是工具 required_scopes + 三态 mode；本表仅展示元数据（中文 label / domain / risk / 描述）。

落地真相（hasn-node `crates/hasn-mcp/src/computer/tools.rs`，CU-P2a 已落；本表是云端侧契约源，
须与之出厂三态对齐——同 imagelab/film/reel 的跨仓零漂移守卫思路）：
- `computer_use:capture`（出厂 **Allow**，risk low）：窗口级截图 + 只读观察——`capture`（须指定目标 App）/
  `list_apps` / `wait`。只读屏幕、不改数据、不外发，默认放行。
- `computer_use:capture_screen`（出厂 **Ask**，risk medium）：`capture_screen` 全屏截图——可能框进其它 App 的
  隐私内容（聊天/密码/邮件），比窗口级敏感，默认逐次审批。
- `computer_use:control`（出厂 **Ask**，risk high）：一切控制动作——`click`/`double_click`/`right_click`/`type`/
  `key`/`scroll`/`drag`/`set_value`/`focus_app`。真实点击/键入到桌面、有副作用，默认审批（`scroll` 仅改视口
  不改数据，在工具粒度出厂放行，属 scope 内例外——scope 默认仍 Ask）。

注：黑名单 App（终端/系统设置/支付密码类）上一切动作在 daemon 侧凌驾放行档强制 Ask（设计 §3.4），
本表只承载**出厂默认**三态；会话×App 白名单与黑名单凌驾属 daemon 执行面（CU-P3），不在 scope 元数据内。
"""

from __future__ import annotations

COMPUTER_USE_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'computer_use:capture': {
        'label_zh': '截取窗口与观察屏幕',
        'label_en': 'Capture window and observe screen',
        'domain': 'computer_use',
        'risk': 'low',
        # 出厂 Allow（省略 default_mode，与 scope_meta 缺省 allow 一致）。
        'description': '以 Agent 身份截取指定 App 窗口并标注可交互元素编号（SOM）、列出运行中的 App、'
        '按需等待界面就绪（只读观察、不改数据、不外发）',
        'description_en': 'Capture a specified app window with numbered interactive elements (SOM), list running apps, and wait for the UI to settle, all as the agent (read-only observation; no data changes, no outbound)',
    },
    'computer_use:capture_screen': {
        'label_zh': '全屏截图',
        'label_en': 'Full-screen capture',
        'domain': 'computer_use',
        'risk': 'medium',
        'default_mode': 'ask',
        'description': '截取整个屏幕（可能框进其它 App 的隐私内容，如聊天/邮件/密码框），比窗口级截图敏感，'
        '默认需主人逐次确认',
        'description_en': "Capture the entire screen (may include other apps' private content such as chats, email, or password fields); more sensitive than window-level capture, so owner confirmation is required by default",
    },
    'computer_use:control': {
        'label_zh': '控制桌面（点击/输入/拖拽）',
        'label_en': 'Control desktop (click/type/drag)',
        'domain': 'computer_use',
        'risk': 'high',
        'default_mode': 'ask',
        'description': '在真实桌面上执行控制动作——单击/双击/右键/键入文本/发送快捷键/滚动/拖拽/设值/前台化 App'
        '（有真实副作用；危险 shell 与破坏性快捷键被工具层硬拦；高危 App 上强制逐次审批），默认需主人确认',
        'description_en': 'Perform control actions on the real desktop — click, double-click, right-click, type text, send hotkeys, scroll, drag, set values, and focus apps (real side effects; dangerous shell patterns and destructive key combos are hard-blocked at the tool layer; high-risk apps always require per-action approval); owner confirmation required by default',
    },
}
