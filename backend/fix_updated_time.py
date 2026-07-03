"""修改所有 app_* 表的 updated_time 列为可空"""
import asyncio

from sqlalchemy import text

from backend.database.db_postgres import create_engine_and_session


async def fix_updated_time() -> None:
    engine, _ = await create_engine_and_session()
    async with engine.begin() as conn:
        # 获取所有 app_platform 相关的表
        result = await conn.execute(text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            AND tablename LIKE 'app_%'
        """))
        tables = [row[0] for row in result]

        for table in tables:
            try:
                await conn.execute(text(f'ALTER TABLE {table} ALTER COLUMN updated_time DROP NOT NULL'))
                print(f'✓ {table}')
            except Exception as e:
                print(f'✗ {table}: {e}')

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(fix_updated_time())
