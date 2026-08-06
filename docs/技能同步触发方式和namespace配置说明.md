# 技能同步触发方式和 namespace/slug 配置说明

**日期**: 2026-05-28  
**状态**: ✅ 已修复

## 一、问题描述

**发现的问题**：
1. ClawHub 同步的技能有 `namespace` 和 `slug` 字段
2. GitHub 本地仓库同步的技能缺少 `namespace` 和 `slug` 字段

**示例对比**：

```
ClawHub 技能：
  skill_id: clawhub/mnetfairy/ai-insurance-advisor
  namespace: clawhub/mnetfairy
  slug: ai-insurance-advisor

GitHub 技能（修复前）：
  skill_id: finance/pitch-deck
  namespace: None
  slug: None
```

## 二、解决方案

### 修复内容

在 `github_sync_service.py` 中为 GitHub 同步的技能添加 `namespace` 和 `slug` 字段：

```python
# _parse_manifest_yaml 方法
skill_data = {
    'skill_id': f"{category}/{skill_id}",
    'namespace': category,  # 使用 category 作为 namespace
    'slug': skill_slug,     # 使用目录名作为 slug
    'category': category,
    # ...
}

# _parse_skill_json 方法
skill_data = {
    'skill_id': f"{category}/{skill_slug}",
    'namespace': category,  # 使用 category 作为 namespace
    'slug': skill_slug,     # 使用目录名作为 slug
    'category': category,
    # ...
}
```

### 字段说明

| 字段 | 说明 | 示例 |
|------|------|------|
| `skill_id` | 技能唯一标识 | `finance/pitch-deck` |
| `namespace` | 命名空间（分类） | `finance` |
| `slug` | 技能标识符（目录名） | `pitch-deck` |
| `category` | 分类 | `finance` |

### 修复后效果

```
GitHub 技能（修复后）：
  skill_id: finance/pitch-deck
  namespace: finance
  slug: pitch-deck
```

## 三、同步触发方式

### 1. 手动触发

#### 技能同步

```bash
# 触发 GitHub 技能同步
POST /api/v1/marketplace/admin/sync/github
Content-Type: application/json
Authorization: Bearer <JWT_TOKEN>

{
  "force": true
}
```

#### 模板同步

```bash
# 触发 GitHub App 模板同步
POST /api/v1/marketplace/admin/sync/github/apps
Content-Type: application/json
Authorization: Bearer <JWT_TOKEN>

{
  "force": true
}
```

#### ClawHub 同步

```bash
# 触发 ClawHub 同步
POST /api/v1/marketplace/admin/sync/clawhub
Content-Type: application/json
Authorization: Bearer <JWT_TOKEN>

{
  "force": true,
  "skill_ids": []  # 可选，指定要同步的技能ID
}
```

### 2. Webhook 自动触发

#### GitHub Webhook 配置

**技能 Webhook**：
- URL: `https://your-domain.com/api/v1/marketplace/webhook/github/skills`
- Content type: `application/json`
- Secret: 配置在环境变量 `GITHUB_WEBHOOK_SECRET`
- Events: `push`

**模板 Webhook**：
- URL: `https://your-domain.com/api/v1/marketplace/webhook/github/apps`
- Content type: `application/json`
- Secret: 配置在环境变量 `GITHUB_WEBHOOK_SECRET`
- Events: `push`

#### Webhook 触发条件

**技能同步**：
- 当 `skills/` 目录下的文件被修改、添加或删除时触发
- 自动检测变更并触发同步

**模板同步**：
- 当 `templates/` 目录下的文件被修改、添加或删除时触发
- 排除 `templates/_` 开头的目录（如 `_base`, `_base_desktop`）

#### Webhook 工作流程

```
GitHub 仓库 push
  ↓
GitHub 发送 Webhook 请求
  ↓
验证签名（HMAC SHA256）
  ↓
检查事件类型（只处理 push）
  ↓
检查变更文件路径
  ↓
如果 skills/ 或 templates/ 有变更
  ↓
触发对应的同步服务
  ↓
返回同步结果
```

### 3. 定时任务（可选）

可以配置定时任务定期同步：

```python
# 使用 APScheduler 或 Celery
@scheduler.scheduled_job('cron', hour=2, minute=0)  # 每天凌晨2点
async def scheduled_sync():
    async with async_db_session() as db:
        # 同步技能
        await github_sync_service.sync_from_github(db, force=False)
        
        # 同步模板
        await github_app_sync_service.sync_from_github(db, force=False)
        
        # 同步 ClawHub
        await clawhub_sync_service.sync_from_clawhub(db, force=False)
```

## 四、同步服务对比

| 同步源 | namespace 格式 | slug 格式 | 触发方式 |
|--------|---------------|-----------|----------|
| **ClawHub** | `clawhub/{owner}` | `{skill-name}` | 手动触发 |
| **GitHub (hasn-hub)** | `{category}` | `{directory-name}` | 手动 + Webhook |
| **本地整理** | `{category}` | `{directory-name}` | 手动 + Webhook |

### 示例对比

```
ClawHub:
  skill_id: clawhub/mnetfairy/ai-insurance-advisor
  namespace: clawhub/mnetfairy
  slug: ai-insurance-advisor

GitHub (huanxing):
  skill_id: huanxing/translator-pro
  namespace: huanxing
  slug: translator-pro

GitHub (community):
  skill_id: community/meeting-scheduler
  namespace: community
  slug: meeting-scheduler

GitHub (category):
  skill_id: finance/pitch-deck
  namespace: finance
  slug: pitch-deck
```

## 五、环境变量配置

### 开发环境

```bash
# hasn-hub 本地路径
HUANXING_HUB_LOCAL_PATH='/Users/mac/openclaw-workspace/huanxing/huanxing-project/hasn-hub'

# GitHub Webhook Secret（可选，开发环境可不配置）
GITHUB_WEBHOOK_SECRET=''

# ClawHub API
CLAWHUB_API_URL='https://clawhub.ai/api/v1'
```

### 生产环境

```bash
# hasn-hub 本地路径
HUANXING_HUB_LOCAL_PATH='/data/hasn-hub'

# GitHub Webhook Secret（生产环境必须配置）
GITHUB_WEBHOOK_SECRET='your-webhook-secret'

# ClawHub API
CLAWHUB_API_URL='https://clawhub.ai/api/v1'
```

## 六、同步日志

所有同步操作都会记录在 `marketplace_sync_log` 表中：

```sql
SELECT 
    id,
    sync_type,
    status,
    items_synced,
    items_failed,
    started_at,
    completed_at
FROM marketplace_sync_log
ORDER BY started_at DESC
LIMIT 10;
```

**sync_type 类型**：
- `github`: GitHub 技能同步
- `clawhub`: ClawHub 同步

## 七、测试验证

### 验证 namespace 和 slug

```python
import asyncio
from backend.database.db import async_db_session
from sqlalchemy import select
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill

async def verify_namespace_slug():
    async with async_db_session() as db:
        # 查询所有技能
        stmt = select(
            MarketplaceSkill.skill_id,
            MarketplaceSkill.namespace,
            MarketplaceSkill.slug
        ).where(
            MarketplaceSkill.namespace.is_(None)
        )
        
        skills_without_namespace = (await db.execute(stmt)).all()
        
        print(f"缺少 namespace 的技能数量: {len(skills_without_namespace)}")
        for skill_id, namespace, slug in skills_without_namespace[:5]:
            print(f"  {skill_id}: namespace={namespace}, slug={slug}")

asyncio.run(verify_namespace_slug())
```

### 触发同步测试

```bash
# 测试技能同步
curl -X POST "http://localhost:8020/api/v1/marketplace/admin/sync/github" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"force": true}'

# 测试模板同步
curl -X POST "http://localhost:8020/api/v1/marketplace/admin/sync/github/apps" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"force": true}'
```

## 八、总结

### 修复内容

✅ 为 GitHub 同步的技能添加 `namespace` 和 `slug` 字段
✅ 使用 `category` 作为 `namespace`
✅ 使用目录名作为 `slug`

### 同步触发方式

1. **手动触发**：通过管理端 API 手动触发同步
2. **Webhook 自动触发**：GitHub 仓库 push 时自动触发
3. **定时任务**（可选）：配置定时任务定期同步

### 下一步

1. 运行一次完整同步，更新所有技能的 `namespace` 和 `slug`
2. 配置生产环境的 GitHub Webhook
3. 验证 Webhook 自动同步功能
