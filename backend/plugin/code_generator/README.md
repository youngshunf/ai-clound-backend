# 一键代码生成器使用指南

## 🎉 功能特性

一键从 SQL 文件生成完整的前后端代码、菜单SQL和字典SQL：

- ✅ **前端代码**：Vue组件 + TypeScript配置 + API + 路由
- ✅ **后端代码**：Model + CRUD + Schema + Service + API
- ✅ **菜单SQL**：父级菜单 + 4个按钮权限
- ✅ **字典SQL**：自动识别 status/type 等字段
- ✅ **智能跳过**：已存在文件不覆盖，不报错
- ✅ **配置驱动**：所有参数通过 config.toml 管理

## 🚀 快速开始

### 基础用法

只需要两个参数就能生成完整的前后端代码：

```bash
cd clound-backend
uv run fba codegen generate --sql-file <SQL文件路径> --app <应用名>
```

**示例：**
```bash
# 基础生成（不执行SQL）
uv run fba codegen generate --sql-file backend/sql/user.sql --app user

# 生成并自动执行SQL
uv run fba codegen generate --sql-file backend/sql/user.sql --app user --execute
```

### 命令参数

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `--sql-file` | ✅ | SQL建表文件路径 | `backend/sql/user.sql` |
| `--app` | ✅ | 应用/模块名称 | `user`, `admin`, `project` |
| `--execute` | ❌ | 自动执行生成的SQL到数据库 | 加上此参数即可 |

## ⚙️ 配置文件

所有其他配置都在 `backend/plugin/code_generator/config.toml` 中管理：

### 路径配置
```toml
[paths]
frontend_dir = "../clound-frontend"      # 前端项目根目录
backend_app_dir = "app"                  # 后端代码生成目录
menu_sql_dir = "backend/sql/generated"   # 菜单SQL输出目录
dict_sql_dir = "backend/sql/generated"   # 字典SQL输出目录
```

### 生成行为配置
```toml
[generation]
auto_execute_menu_sql = false            # 是否自动执行菜单SQL
auto_execute_dict_sql = false            # 是否自动执行字典SQL
existing_file_behavior = "skip"          # 文件已存在时: skip/overwrite/backup
generate_backend = true                  # 是否生成后端代码
generate_frontend = true                 # 是否生成前端代码
generate_menu_sql = true                 # 是否生成菜单SQL
generate_dict_sql = true                 # 是否生成字典SQL
```

### 字典自动生成配置
```toml
[dict]
# 自动生成字典的字段名模式
auto_dict_patterns = [
    "status",
    "type",
    "state",
    "category",
    "level",
]

# 默认字典选项（用于 status 字段）
default_status_options = [
    { label = "启用", value = 1, color = "green" },
    { label = "禁用", value = 0, color = "red" },
]
```

## 📦 生成内容

### 1. 前端代码（自动跳过已存在文件）

```
clound-frontend/apps/web-antd/src/
├── views/<app>/<表名>/
│   ├── index.vue      # 主页面（列表+表单）
│   └── data.ts        # 表格列和表单配置
├── api/<app>.ts   # API接口定义
└── router/routes/modules/<app>.ts  # 路由配置
```

### 2. 后端代码（全部生成）

```
backend/app/<app>/
├── model/<表名>.py             # SQLAlchemy模型
├── crud/crud_<表名>.py       # CRUD操作
├── schema/<表名>.py           # Pydantic Schema
├── service/<表名>_service.py  # 业务逻辑
├── api/v1/<表名>.py          # API路由
└── sql/                        # 初始化SQL（MySQL/PostgreSQL）
```

### 3. SQL文件

- ✅ `backend/sql/generated/<表名>_menu.sql` - 菜单和权限
  - 1个父级菜单
  - 4个按钮权限（新增、编辑、删除、查看）
  
- ✅ `backend/sql/generated/<表名>_dict.sql` - 数据字典
  - 自动为 `status`、`type`、`state`、`level` 等字段生成字典

## 🎯 使用示例

### 示例1：新建用户管理模块

```bash
# 1. 准备SQL文件：backend/sql/users.sql
# 2. 执行生成命令
uv run fba codegen generate --sql-file backend/sql/users.sql --app user

# 生成结果：
# - 前端：views/user/users/index.vue
# - 后端：app/user/model/users.py
# - SQL：sql/generated/users_menu.sql
```

### 示例2：快速原型（生成并执行SQL）

```bash
# 适合开发环境快速迭代
uv run fba codegen generate --sql-file backend/sql/products.sql --app product --execute

# 会自动执行：
# - 菜单SQL插入到数据库
# - 字典SQL插入到数据库
```

### 示例3：批量生成

```bash
# 一次生成多个表
for sql_file in backend/sql/*.sql; do
  table_name=$(basename "$sql_file" .sql)
  uv run fba codegen generate --sql-file "$sql_file" --app admin
done
```

## ❓ 常见问题

### Q1: 生成的文件位置不对？

**A:** 检查 `config.toml` 中的 `frontend_dir` 和 `backend_app_dir` 配置。

### Q2: 没有生成字典SQL？

**A:** 确保字段名包含 `status`、`type`、`state` 或 `level`。可在 `config.toml` 中自定义：
```toml
[dict]
auto_dict_patterns = ["status", "type", "state", "level", "category"]
```

### Q3: SQL执行失败？

**A:** 如果数据库表结构不匹配，命令会显示警告但不中断。手动执行生成的SQL文件即可。

### Q4: 如何覆盖已存在的文件？

**A:** 在 `config.toml` 中设置：
```toml
[generation]
existing_file_behavior = "overwrite"
```

### Q5: 后端代码生成失败？

**A:** 确保：
1. Python 模板文件存在：`templates/python/*.jinja`
2. 数据库 `gen_business` 表结构匹配
3. SQL文件格式正确

## 📚 相关文档

- **快速参考**：`backend/代码生成使用说明.md`
- **完整指南**：项目根目录下的 `CODE_GENERATION_GUIDE.md`
- **原框架文档**：`docs/代码生成/README.md`

## 🔧 高级配置

### 自定义字典选项

```toml
[dict]
# 状态字段默认选项
default_status_options = [
    { label = "开启", value = 1, color = "blue" },
    { label = "关闭", value = 0, color = "gray" },
    { label = "禁用", value = -1, color = "red" },
]

# 类型字段默认选项
default_type_options = [
    { label = "普通", value = 1, color = "blue" },
    { label = "高级", value = 2, color = "gold" },
    { label = "VIP", value = 3, color = "purple" },
]
```

### 菜单父级关联

```toml
[menu]
parent_menu_id = 100  # 设置父级菜单ID
menu_sort_start = 200 # 菜单排序起始值
```

---

**需要帮助？** 查看完整文档或联系项目维护者
