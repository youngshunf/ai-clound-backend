# 记忆系统数据库迁移 - 快速参考

## 快速执行

```bash
cd huanxing-cloud-backend
uv run python backend/sql/migrations/run_migration.py
```

## 验证表是否创建

```bash
uv run python -c "
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def check():
    engine = create_async_engine('postgresql+asyncpg://postgres:@localhost:15432/huanxing')
    async with engine.connect() as conn:
        result = await conn.execute(text('''
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = '\''public'\'' 
            AND (table_name LIKE '\''memory_%'\'' OR table_name = '\''episodic_turns'\'' OR table_name = '\''semantic_facts'\'')
            ORDER BY table_name;
        '''))
        print('已创建的表:')
        for row in result:
            print('  ✓', row[0])
    await engine.dispose()

asyncio.run(check())
"
```

## 创建的表

1. ✅ `memory_namespace_revisions` - 命名空间 revision 表
2. ✅ `episodic_turns` - 原始对话 turn
3. ✅ `semantic_facts` - 语义事实
4. ✅ `memory_events` - 时序事件
5. ✅ `memory_extraction_jobs` - 提取任务

## 文件位置

- **迁移 SQL**: `backend/sql/migrations/2026-05-26-memory-system-tables.sql`
- **执行脚本**: `backend/sql/migrations/run_migration.py`
- **详细文档**: `backend/sql/migrations/README.md`
- **迁移报告**: `backend/sql/migrations/2026-05-26-migration-report.md`
- **当前事实源**: `docs/产品与技术/技术设计/02-平台能力/记忆与知识库/README.md`

## 回滚

```sql
DROP TABLE IF EXISTS "public"."memory_extraction_jobs";
DROP TABLE IF EXISTS "public"."memory_events";
DROP TABLE IF EXISTS "public"."semantic_facts";
DROP TABLE IF EXISTS "public"."episodic_turns";
DROP TABLE IF EXISTS "public"."memory_namespace_revisions";
```

## 常见问题

### Q: 迁移后后端仍然报错？
A: 重启后端服务：`python backend/run.py`

### Q: 如何验证表是否创建成功？
A: 运行上面的验证脚本，或使用 psql：
```bash
psql -h localhost -p 15432 -U postgres -d huanxing -c "\dt memory_*"
```

### Q: 可以重复执行迁移吗？
A: 可以，所有语句都使用了 `IF NOT EXISTS`，重复执行不会报错。

### Q: 生产环境如何执行？
A: 修改环境变量后执行：
```bash
export DB_HOST=117.72.92.229
export DB_PORT=5432
export DB_PASSWORD=your_password
uv run python backend/sql/migrations/run_migration.py
```
