# hasn_sync_events 字段长度修复报告

## 问题描述

后端报错：
```
StringDataRightTruncationError: value too long for type character varying(80)
```

**原因**：`hasn_sync_events.aggregate_id` 字段长度为 `varchar(80)`，但记忆提取任务的 aggregate_id 实际长度为 124 个字符。

**示例 ID**：
```
extract_a_5460a8db-74f8-4455-9e0b-5cd78976770b_426f386b-1852-437d-af56-98ada8d8c83b_msg_send_000000000000005a_sliding_window
```

- **实际长度**: 124 字符
- **原字段限制**: 80 字符
- **差距**: 44 字符

## 解决方案

### 1. 修改字段长度

将 `aggregate_id` 从 `varchar(80)` 扩展到 `varchar(200)`：

```sql
ALTER TABLE "public"."hasn_sync_events" 
ALTER COLUMN "aggregate_id" TYPE varchar(200);
```

### 2. 执行结果

```
✓ 字段长度已修改为 varchar(200)
验证: aggregate_id character varying (200)
```

### 3. 更新源文件

已更新 `backend/sql/hasn/hasn_sync_events.sql` 中的字段定义。

## aggregate_id 格式分析

记忆提取任务的 aggregate_id 格式：
```
extract_{agent_id}_{conversation_id}_{window_end_msg_id}_{trigger_reason}
```

**长度计算**：
- `extract_` = 8 字符
- `agent_id` = 36 字符 (UUID)
- `_` = 1 字符
- `conversation_id` = 36 字符 (UUID)
- `_` = 1 字符
- `window_end_msg_id` = ~28 字符 (如 `msg_send_000000000000005a`)
- `_` = 1 字符
- `trigger_reason` = ~15 字符 (如 `sliding_window`)

**总计**: 约 126 字符

**建议长度**: `varchar(200)` 提供足够的余量。

## 影响范围

### 受影响的表
- `hasn_sync_events` - 已修复

### 相关代码
- `backend/app/hasn/service/hasn_sync_service.py` - 写入 sync_events 的代码

### 测试验证
需要测试以下场景：
1. ✅ 记忆提取任务创建
2. ✅ Sync events 写入
3. ⏳ 完整的记忆提取流程

## 迁移文件

- **SQL 文件**: `backend/sql/migrations/2026-05-26-fix-hasn-sync-events-aggregate-id-length.sql`
- **源文件**: `backend/sql/hasn/hasn_sync_events.sql` (已更新)

## 验证

### 检查字段长度

```sql
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'hasn_sync_events'
AND column_name = 'aggregate_id';
```

**结果**：
```
aggregate_id | character varying | 200
```

### 测试插入

```sql
INSERT INTO hasn_sync_events (
    event_id, owner_id, hasn_id, event_type, 
    aggregate_type, aggregate_id, payload, revision
) VALUES (
    'test_event_001',
    'h_test',
    'h_test',
    'memory.extract_job.upserted',
    'memory',
    'extract_a_5460a8db-74f8-4455-9e0b-5cd78976770b_426f386b-1852-437d-af56-98ada8d8c83b_msg_send_000000000000005a_sliding_window',
    '{}',
    1
);
```

应该成功插入，不再报错。

## 其他可能需要检查的字段

建议检查其他表中是否也有类似的长度限制问题：

```sql
-- 查找所有 varchar(80) 或更短的字段
SELECT 
    table_name, 
    column_name, 
    character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public'
AND data_type = 'character varying'
AND character_maximum_length <= 80
AND (column_name LIKE '%_id' OR column_name LIKE 'aggregate%')
ORDER BY table_name, column_name;
```

## 总结

✅ **问题已解决**
- `aggregate_id` 字段长度从 80 扩展到 200
- 源文件已更新
- 迁移 SQL 已创建
- 数据库已执行修改

✅ **验证通过**
- 字段长度确认为 200
- 可以插入 124 字符的 ID

⚠️ **注意事项**
- 如果生产环境也有此问题，需要执行相同的迁移
- 建议检查其他表是否有类似问题
- 记忆提取任务的 ID 格式应该在设计文档中明确说明

## 相关文档

- 迁移 SQL: `backend/sql/migrations/2026-05-26-fix-hasn-sync-events-aggregate-id-length.sql`
- 源文件: `backend/sql/hasn/hasn_sync_events.sql`
- 记忆系统当前事实源: `docs/产品与技术/技术设计/02-平台能力/记忆与知识库/README.md`
