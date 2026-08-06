# HASN 数据库迁移

## 目录说明

本目录存放 HASN 相关的数据库迁移 SQL 文件。

## 迁移规范

### 文件命名

```
YYYY-MM-DD-description.sql
```

例如：
- `2026-05-26-memory-system-tables.sql`
- `2026-05-26-add-marketplace-skill-multilang-columns.sql`

### 迁移内容

- **必须使用 PostgreSQL 语法**（不是 MySQL）
- **必须使用 `CREATE TABLE IF NOT EXISTS`**，避免重复执行报错
- **必须使用 `CREATE INDEX IF NOT EXISTS`**
- **必须使用 `COMMENT ON COLUMN`** 语法添加注释
- **时间字段使用 `created_time`/`updated_time`**（timestamptz）

### SQL 语法示例

```sql
-- 创建表
CREATE TABLE IF NOT EXISTS "public"."example_table" (
  "id"           varchar(40) PRIMARY KEY,
  "name"         varchar(100) NOT NULL,
  "status"       varchar(16) NOT NULL DEFAULT 'active',
  "created_time" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time" timestamptz(6),
  CHECK ("status" IN ('active', 'inactive'))
);

-- 创建索引
CREATE INDEX IF NOT EXISTS "idx_example_status"
  ON "public"."example_table"("status", "created_time" DESC);

-- 添加注释
COMMENT ON TABLE "public"."example_table" IS '示例表';
COMMENT ON COLUMN "public"."example_table"."id" IS 'ID';
COMMENT ON COLUMN "public"."example_table"."name" IS '名称';
COMMENT ON COLUMN "public"."example_table"."status" IS '状态 (active/inactive)';
```

## 执行迁移

### 方式一：使用脚本（推荐）

```bash
cd backend/sql/migrations

# 设置数据库密码（可选）
export DB_PASSWORD="your_password"

# 执行迁移
./run_migration.sh
```

### 方式二：手动执行

```bash
# 本地开发环境（端口 15432）
psql -h localhost -p 15432 -U postgres -d huanxing -f 2026-05-26-memory-system-tables.sql

# 生产环境（端口 5432）
psql -h 117.72.92.229 -p 5432 -U postgres -d huanxing -f 2026-05-26-memory-system-tables.sql
```

## 当前迁移列表

### 2026-05-26: 记忆系统核心表

文件：`2026-05-26-memory-system-tables.sql`

创建的表：
1. `memory_namespace_revisions` - 命名空间 revision 表
2. `episodic_turns` - 原始对话 turn
3. `semantic_facts` - 语义事实
4. `memory_events` - 时序事件
5. `memory_extraction_jobs` - 提取任务

### 2026-05-26: 技能市场多语言字段

文件：`2026-05-26-add-marketplace-skill-multilang-columns.sql`

修改的表：
- `marketplace_skill` - 添加多语言字段

## 验证迁移

执行迁移后，验证表是否创建成功：

```sql
-- 查看所有记忆相关的表
SELECT 
    table_name,
    pg_size_pretty(pg_total_relation_size(quote_ident(table_name)::regclass)) as size
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name LIKE 'memory_%' OR table_name LIKE '%_turns' OR table_name LIKE 'semantic_%'
ORDER BY table_name;

-- 查看表结构
\d+ memory_namespace_revisions
\d+ episodic_turns
\d+ semantic_facts
\d+ memory_events
\d+ memory_extraction_jobs
```

## 回滚

如果需要回滚迁移，手动删除表：

```sql
DROP TABLE IF EXISTS "public"."memory_extraction_jobs";
DROP TABLE IF EXISTS "public"."memory_events";
DROP TABLE IF EXISTS "public"."semantic_facts";
DROP TABLE IF EXISTS "public"."episodic_turns";
DROP TABLE IF EXISTS "public"."memory_namespace_revisions";
```

## 注意事项

1. **生产环境迁移前必须备份数据库**
2. **迁移 SQL 必须幂等**（可重复执行）
3. **大表迁移需要评估执行时间**
4. **索引创建可能需要较长时间**
5. **迁移后需要验证应用功能**

## 相关文档

- 记忆系统当前事实源：`docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md`
- 后端开发规范：`CLAUDE.md`
