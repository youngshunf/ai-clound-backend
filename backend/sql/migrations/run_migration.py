#!/usr/bin/env python3
"""
HASN 记忆系统数据库迁移执行脚本
"""

import os
import sys

from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# 数据库配置
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "15432")
DB_NAME = os.getenv("DB_NAME", "huanxing")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


async def run_migration() -> None:
    """执行数据库迁移"""
    print("=" * 50)
    print("HASN 记忆系统数据库迁移")
    print("=" * 50)
    print()
    print("数据库配置:")
    print(f"  Host: {DB_HOST}")
    print(f"  Port: {DB_PORT}")
    print(f"  Database: {DB_NAME}")
    print(f"  User: {DB_USER}")
    print()

    # 读取迁移 SQL 文件
    migration_file = Path(__file__).parent / "2026-05-26-memory-system-tables.sql"

    if not migration_file.exists():
        print(f"❌ 错误: 迁移文件不存在: {migration_file}")
        sys.exit(1)

    print(f"📄 读取迁移文件: {migration_file.name}")
    sql_content = migration_file.read_text(encoding="utf-8")

    # 创建数据库引擎
    engine = create_async_engine(DATABASE_URL, echo=False)

    try:
        async with engine.begin() as conn:
            print("🔄 执行迁移...")
            print()

            # 分割 SQL 语句（按分号分割，但保留完整的语句）
            statements = []
            current_statement = []

            for line in sql_content.split('\n'):
                line = line.strip()
                # 跳过空行和纯注释行
                if not line or line.startswith('--'):
                    continue

                current_statement.append(line)

                # 如果行以分号结尾，表示语句结束
                if line.endswith(';'):
                    stmt = ' '.join(current_statement)
                    if stmt and not stmt.startswith('--'):
                        statements.append(stmt)
                    current_statement = []

            # 执行每条语句
            for statement in statements:
                try:
                    await conn.execute(text(statement))

                    # 打印重要的操作
                    if 'CREATE TABLE' in statement:
                        # 提取表名
                        parts = statement.split('CREATE TABLE')[1].split('(')[0]
                        table_name = parts.replace('IF NOT EXISTS', '').replace('"', '').replace('public.', '').strip()
                        print(f"  ✓ 创建表: {table_name}")
                    elif 'CREATE INDEX' in statement and 'idx_memory' in statement:
                        # 只打印记忆相关的索引
                        pass  # 不打印索引创建，太多了

                except Exception as e:
                    error_msg = str(e)
                    # 忽略 "already exists" 错误
                    if 'already exists' not in error_msg:
                        print(f"  ⚠️  警告: {error_msg[:100]}")

            print()
            print("✅ 迁移执行成功！")
            print()

        # 验证表是否创建成功
        print("🔍 验证表创建...")
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT
                    table_name,
                    pg_size_pretty(pg_total_relation_size(quote_ident(table_name)::regclass)) as size
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name IN (
                    'memory_namespace_revisions',
                    'episodic_turns',
                    'semantic_facts',
                    'memory_events',
                    'memory_extraction_jobs'
                )
                ORDER BY table_name;
            """))

            rows = result.fetchall()
            if rows:
                print()
                print("表名                          | 大小")
                print("-" * 50)
                for row in rows:
                    print(f"{row[0]:<30} | {row[1]}")
                print()
                print(f"✅ 成功创建 {len(rows)} 个表！")
            else:
                print("⚠️  警告: 未找到任何表")

    except Exception as e:
        print()
        print(f"❌ 迁移执行失败: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_migration())
