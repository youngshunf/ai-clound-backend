# 一键代码生成使用指南

## 🎯 功能概述

一键从 SQL 文件生成完整的前后端代码、菜单SQL和字典SQL。

### ✅ 已实现功能

1. **前端代码生成** - Vue组件、TypeScript API、路由配置
2. **菜单SQL生成** - 自动生成菜单和按钮权限
3. **字典SQL生成** - 自动识别status/type等字段生成字典数据
4. **目录结构** - `app名/表名` 的规范化目录结构
5. **智能跳过** - 已存在文件不覆盖
6. **自动执行SQL** - 可选自动将SQL插入数据库

### ⚠️ 限制

- 后端代码生成需使用原框架命令：`fba codegen import` + `fba codegen`

## 📦 命令格式

```bash
uv run fba codegen generate --sql-file <SQL文件路径> --app <应用名称> [--execute]
```

### 参数说明

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `--sql-file` | ✅ | SQL文件路径（相对或绝对） | `backend/sql/projects.sql` |
| `--app` | ✅ | 应用名称，用于分组管理 | `admin`, `user`, `system` |
| `--execute` | ❌ | 自动执行生成的SQL到数据库 | 默认不执行 |

## 🚀 快速开始

### 1. 准备 SQL 文件

支持 PostgreSQL 和 MySQL 的 CREATE TABLE 语句：

```sql
-- PostgreSQL 示例
CREATE TABLE "public"."projects" (
  "uid" varchar(36) NOT NULL,
  "user_uid" varchar(36) NOT NULL,
  "brand_name" varchar(100),
  "status" integer DEFAULT 1,
  "created_time" timestamp,
  "updated_time" timestamp,
  PRIMARY KEY ("uid")
);

COMMENT ON COLUMN "public"."projects"."uid" IS '项目ID';
COMMENT ON COLUMN "public"."projects"."brand_name" IS '品牌名称';
COMMENT ON COLUMN "public"."projects"."status" IS '状态';
```

### 2. 执行生成命令

```bash
cd clound-backend
uv run fba codegen generate --sql-file backend/sql/projects.sql --app admin
```

### 3. 查看生成结果

```
============================================================
  一键代码生成器 - FastAPI Best Architecture
============================================================

📄 步骤 1/5: 解析SQL文件...
   ✓ 表名: projects
   ✓ 注释: None
   ✓ 字段数: 18
   ✓ 数据库: postgresql

🎨 步骤 2/5: 生成前端代码...
   ✓ 前端代码生成成功

🔧 步骤 3/5: 生成后端代码...
   ⚠ 后端代码生成失败: 
   ℹ️ 后端代码需要使用 fba codegen import + fba codegen 命令

📋 步骤 4/5: 生成菜单SQL...
   ✓ 菜单SQL已保存: /path/to/projects_menu.sql

📚 步骤 5/5: 生成字典SQL...
   ⚠ 未找到需要生成字典的字段

============================================================
✨ 代码生成完成！
============================================================

📦 生成的文件结构:
   前端: apps/web-antd/src/views/admin/projects/
   API:  apps/web-antd/src/api/admin.ts
   路由: apps/web-antd/src/router/routes/modules/admin.ts
   后端: backend/app/admin/projects/
   SQL:  /path/to/projects_menu.sql
```

## 📂 生成的文件结构

### 前端代码

```
clound-frontend/apps/web-antd/src/
├── views/
│   └── admin/          # 应用名
│       └── projects/   # 表名
│           ├── index.vue    # 主页面（列表+表单）
│           └── data.ts      # 表格列和表单配置
├── api/
│   └── admin.ts        # API接口定义
└── router/routes/modules/
    └── admin.ts        # 路由配置
```

### SQL文件

```
clound-backend/backend/sql/generated/
├── projects_menu.sql   # 菜单和权限SQL
└── projects_dict.sql   # 字典数据SQL（如果有）
```

### 后端代码（需单独生成）

```
clound-backend/backend/app/
└── admin/              # 应用名
    └── projects/       # 表名
        ├── __init__.py
        ├── model.py    # SQLAlchemy模型
        ├── schema.py   # Pydantic schema
        ├── crud.py     # CRUD操作
        ├── service.py  # 业务逻辑
        └── api.py      # API路由
```

## 🔧 配置文件

配置文件位置：`backend/plugin/code_generator/config.toml`

### 主要配置项

```toml
[paths]
frontend_dir = "../clound-frontend"           # 前端根目录
backend_app_dir = "app"                       # 后端app目录
menu_sql_dir = "backend/sql/generated"        # 菜单SQL输出目录
dict_sql_dir = "backend/sql/generated"        # 字典SQL输出目录

[generation]
existing_file_behavior = "skip"               # skip/overwrite/backup
auto_execute_menu_sql = false                 # 是否自动执行菜单SQL
auto_execute_dict_sql = false                 # 是否自动执行字典SQL
generate_backend = true                       # 是否生成后端代码
generate_frontend = true                      # 是否生成前端代码
generate_menu_sql = true                      # 是否生成菜单SQL
generate_dict_sql = true                      # 是否生成字典SQL

[backend]
default_db_schema = "fba"                     # 默认数据库schema
api_version = "v1"                            # API版本

[frontend]
default_icon = "lucide:list"                  # 默认菜单图标

[menu]
parent_menu_id = 0                            # 父级菜单ID（0表示顶级）
menu_sort_start = 100                         # 菜单排序起始值

[dict]
auto_dict_patterns = ["status", "type", "state", "level"]  # 自动识别的字段模式

# 状态字段默认选项
default_status_options = [
    { label = "启用", value = 1, color = "green" },
    { label = "禁用", value = 0, color = "red" },
]

# 类型字段默认选项
default_type_options = [
    { label = "类型1", value = 1, color = "blue" },
    { label = "类型2", value = 2, color = "green" },
]
```

## 📝 使用场景

### 场景1：新建模块（只生成不执行SQL）

```bash
uv run fba codegen generate --sql-file backend/sql/user.sql --app user
# 生成代码和SQL文件，但不执行SQL
# 手动检查SQL后，再使用数据库客户端执行
```

### 场景2：快速原型（生成并执行SQL）

```bash
uv run fba codegen generate --sql-file backend/sql/project.sql --app admin --execute
# 生成代码并自动执行SQL到数据库
# 适合开发环境快速迭代
```

### 场景3：生成后端代码

```bash
# 步骤1：导入表信息
uv run fba codegen import --app admin --tn projects

# 步骤2：选择业务编号生成代码
uv run fba codegen
```

## 🔍 常见问题

### Q1: 为什么后端代码生成失败？

**A:** 后端代码生成依赖框架原有的模板系统和数据库业务表，需要使用两步流程：
1. `fba codegen import` 导入表信息
2. `fba codegen` 选择业务编号生成代码

### Q2: 如何修改字典自动生成的选项？

**A:** 编辑 `config.toml` 中的 `[dict]` 部分：
- 修改 `auto_dict_patterns` 添加自定义字段模式
- 修改 `default_status_options` 和 `default_type_options` 自定义选项

### Q3: 生成的文件被覆盖了怎么办？

**A:** 默认行为是跳过已存在文件。如果被覆盖，可能配置文件设置了 `existing_file_behavior = "overwrite"`。
改为 `"skip"` 即可保护现有代码。

### Q4: 如何修改生成的前端组件样式？

**A:** 编辑模板文件：
- Vue组件：`backend/plugin/code_generator/templates/vue/index.vue.jinja`
- 数据配置：`backend/plugin/code_generator/templates/typescript/data.ts.jinja`

### Q5: 菜单SQL如何关联到父级菜单？

**A:** 在 `config.toml` 中设置 `parent_menu_id`：
```toml
[menu]
parent_menu_id = 123  # 父级菜单的ID
```

## 📚 相关命令

```bash
# 原框架代码生成命令
uv run fba codegen import --app <应用名> --tn <表名>  # 导入表信息
uv run fba codegen                                    # 交互式生成代码
uv run fba codegen -p                                 # 预览将要生成的文件

# 新的一键生成命令
uv run fba codegen generate --sql-file <SQL文件> --app <应用名>           # 基础生成
uv run fba codegen generate --sql-file <SQL文件> --app <应用名> --execute # 生成并执行SQL
```

## 🎉 最佳实践

1. **SQL文件规范**
   - 使用清晰的表注释和列注释
   - 字段命名使用下划线分隔（snake_case）
   - 时间字段使用 `created_time`, `updated_time` 命名

2. **目录组织**
   - 按业务模块划分应用名（app）
   - 一个表对应一个模块（table name）
   - 相关功能放在同一应用下

3. **版本控制**
   - 生成的代码纳入版本控制
   - 生成的SQL文件也纳入版本控制
   - 配置文件根据环境调整

4. **团队协作**
   - 统一配置文件内容
   - 使用相同的命名规范
   - 代码生成后进行Code Review

---

**需要帮助？** 查看完整文档或提交Issue
