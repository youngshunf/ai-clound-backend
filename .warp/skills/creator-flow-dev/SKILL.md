---
name: creator-flow-dev
description: CreatorFlow 云端项目开发工作流指南。当用户需要开发新功能模块、创建数据库表、生成 CRUD 代码时使用此技能。
metadata:
  author: CreatorFlow Team
  version: "1.0"
  short-description: CreatorFlow 全栈开发工作流
---

# CreatorFlow 开发工作流

本技能指导 Agent 在 CreatorFlow 云端项目中进行规范化开发。

## 项目结构

```
creator-flow/
├── clound-backend/      # FastAPI 后端
│   ├── backend/
│   │   ├── app/         # 业务模块
│   │   ├── plugin/      # 插件（含代码生成器）
│   │   └── sql/generated/  # 生成的 SQL 文件
│   └── pyproject.toml
└── clound-frontend/     # Vue3 前端 (Vben Admin)
    └── apps/web-antd/src/
        ├── api/         # API 接口
        └── views/       # 页面组件
```

## 开发工作流程

### 第一步：设计数据库表

在 `clound-backend/backend/sql/` 创建 SQL 文件，遵循以下规范：

```sql
-- 表名使用下划线命名，如 user_subscription
CREATE TABLE IF NOT EXISTS user_subscription (
    -- 主键必须为 id，使用 SERIAL
    id SERIAL PRIMARY KEY,
    
    -- 外键字段以 _id 结尾
    user_id INTEGER NOT NULL,
    
    -- 字典字段命名规范（会自动生成字典）：
    -- status, state, type, category, level, tier
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    tier VARCHAR(20) NOT NULL DEFAULT 'free',
    
    -- 布尔字段以 is_ 或 has_ 开头
    is_active BOOLEAN DEFAULT TRUE,
    auto_renew BOOLEAN DEFAULT FALSE,
    
    -- 时间戳字段（必须包含，会自动排除在表单外）
    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_time TIMESTAMP DEFAULT NULL
);

-- 重要：添加表注释（会作为菜单标题）
COMMENT ON TABLE user_subscription IS '用户订阅表';

-- 重要：添加列注释（会作为字段标签）
-- 格式：简短标签 或 简短标签 (详细说明)
-- 括号内说明会在前端自动省略
COMMENT ON COLUMN user_subscription.user_id IS '用户 ID';
COMMENT ON COLUMN user_subscription.status IS '订阅状态';
COMMENT ON COLUMN user_subscription.tier IS '订阅等级';
```

### 字典字段命名规范

以下字段名会被自动识别为字典字段，并生成对应的字典 SQL：

| 模式 | 示例 | 说明 |
|------|------|------|
| `status` | status, user_status | 状态字典 |
| `state` | state, order_state | 状态字典 |
| `type` | type, payment_type | 类型字典 |
| `category` | category | 分类字典 |
| `level` | level, user_level | 等级字典 |
| `tier` | tier, subscription_tier | 层级字典 |

生成的字典代码格式为：`{app}_{field_name}`，如 `llm_status`

### 第二步：导入表到代码生成器

```bash
cd clound-backend

# 将表结构导入到 gen_business/gen_column 表
uv run fba codegen import --table user_subscription --app llm
```

### 第三步：一键生成代码

```bash
# 生成完整代码（前端 + 后端 + 菜单SQL + 字典SQL）
uv run fba codegen generate --table user_subscription --app llm

# 强制覆盖已有文件
uv run fba codegen generate --table user_subscription --app llm --force
```

生成的文件包括：

**后端（已有则跳过）：**
- `backend/app/{app}/model/{table}.py` - SQLAlchemy 模型
- `backend/app/{app}/schema/{table}.py` - Pydantic Schema
- `backend/app/{app}/crud/crud_{table}.py` - CRUD 操作
- `backend/app/{app}/service/{table}_service.py` - 业务服务
- `backend/app/{app}/api/v1/{table}.py` - API 路由

**前端：**
- `apps/web-antd/src/views/{app}/{table}/index.vue` - 页面组件
- `apps/web-antd/src/views/{app}/{table}/data.ts` - 表格/表单配置
- `apps/web-antd/src/api/{app}/{table}.ts` - API 接口

**SQL：**
- `backend/sql/generated/{table}_menu.sql` - 菜单初始化
- `backend/sql/generated/{table}_dict.sql` - 字典初始化

### 第四步：执行生成的 SQL

```bash
# 菜单 SQL（自动执行或手动）
uv run fba --sql backend/sql/generated/user_subscription_menu.sql

# 字典 SQL
uv run fba --sql backend/sql/generated/user_subscription_dict.sql
```

**注意**：菜单和字典 SQL 都是幂等的，可以重复执行。

### 第五步：注册路由（如果是新模块）

编辑 `backend/app/{app}/api/router.py`：

```python
from backend.app.{app}.api.v1.{table} import router as {table}_router

v1.include_router({table}_router, prefix='/{table/path}s')
```

路由路径规范：`user_subscription` → `/user/subscriptions`（下划线转斜杠，复数）

### 第六步：业务开发

基础 CRUD 已生成，现在可以：

1. **扩展后端服务** - 在 `service/{table}_service.py` 添加业务逻辑
2. **扩展前端页面** - 在 `views/{app}/{table}/` 添加自定义功能
3. **添加权限控制** - 使用生成的权限标识 `{table}:add/edit/del/get`

## 前端字典使用

生成的代码会自动使用字典组件：

```typescript
// data.ts 中的字典字段会自动使用 getDictOptions
import { getDictOptions } from '#/utils/dict';

// 查询表单
{
  component: 'Select',
  fieldName: 'status',
  label: '状态',
  componentProps: {
    options: getDictOptions('llm_status'),
  },
}

// 表格列
{
  field: 'status',
  title: '状态',
  cellRender: {
    name: 'CellTag',
    options: getDictOptions('llm_status'),
  },
}
```

## 常用命令速查

```bash
# 代码生成
uv run fba codegen generate --table TABLE --app APP [--force]

# 导入表
uv run fba codegen import --table TABLE --app APP

# 执行 SQL
uv run fba --sql path/to/file.sql

# 启动后端
uv run fba run

# 启动前端
cd ../clound-frontend && pnpm dev
```

## 注意事项

1. **字段注释很重要** - 会影响前端显示的标签
2. **遵循命名规范** - 字典字段命名会触发自动字典生成
3. **先导入再生成** - 确保表已导入到 gen_business
4. **SQL 幂等** - 菜单/字典 SQL 可重复执行
5. **路由注册** - 新模块需要手动注册路由
