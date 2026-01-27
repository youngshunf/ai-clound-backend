"""CLI commands for frontend code generation."""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import cappa

from backend.common.exception.errors import BaseExceptionError
from backend.database.db import async_db_session
from backend.plugin.code_generator.frontend.generator import frontend_generator
from backend.plugin.code_generator.frontend.menu_generator import (
    execute_menu_sql,
    generate_menu_sql,
    save_menu_sql_to_file,
)
from backend.plugin.code_generator.parser.sql_parser import sql_parser
from backend.utils.console import console


@cappa.command(name='frontend', help='Generate frontend CRUD code', default_long=True)
@dataclass
class CodegenFrontend:
    """Generate frontend CRUD code from SQL file or database table."""

    app: Annotated[
        str,
        cappa.Arg(help='Application/module name (required)'),
    ]
    sql_file: Annotated[
        Path | None,
        cappa.Arg(help='Path to SQL file (mutually exclusive with --table)'),
    ] = None
    table: Annotated[
        str | None,
        cappa.Arg(help='Table name for DB introspection (mutually exclusive with --sql-file)'),
    ] = None
    db: Annotated[
        str,
        cappa.Arg(default='fba', help='Database schema name'),
    ] = 'fba'
    module: Annotated[
        str | None,
        cappa.Arg(help='Module name within app (defaults to table name)'),
    ] = None
    output_dir: Annotated[
        Path | None,
        cappa.Arg(help='Frontend output directory (auto-detected if omitted)'),
    ] = None
    force: Annotated[
        bool,
        cappa.Arg(default=False, help='Overwrite existing files without prompting'),
    ] = False

    def __post_init__(self):
        """Validate arguments."""
        if not self.sql_file and not self.table:
            raise cappa.Exit('Either --sql-file or --table must be specified', code=1)
        if self.sql_file and self.table:
            raise cappa.Exit('--sql-file and --table are mutually exclusive', code=1)
        if self.sql_file and not self.sql_file.exists():
            raise cappa.Exit(f'SQL file not found: {self.sql_file}', code=1)

    async def __call__(self) -> None:
        """Execute frontend code generation."""
        try:
            if self.sql_file:
                await frontend_generator.generate_from_sql(
                    sql_file=self.sql_file,
                    app=self.app,
                    module=self.module,
                    output_dir=self.output_dir,
                    force=self.force,
                )
            else:
                async with async_db_session() as db:
                    await frontend_generator.generate_from_db(
                        table=self.table,
                        db_schema=self.db,
                        app=self.app,
                        module=self.module,
                        output_dir=self.output_dir,
                        force=self.force,
                        db=db,
                    )
        except Exception as e:
            raise cappa.Exit(e.msg if isinstance(e, BaseExceptionError) else str(e), code=1)


@cappa.command(name='menu', help='Generate menu SQL', default_long=True)
@dataclass
class CodegenMenu:
    """Generate menu SQL for frontend CRUD pages."""

    app: Annotated[
        str,
        cappa.Arg(help='Application/module name (required)'),
    ]
    sql_file: Annotated[
        Path | None,
        cappa.Arg(help='Path to SQL file (mutually exclusive with --table)'),
    ] = None
    table: Annotated[
        str | None,
        cappa.Arg(help='Table name for DB introspection (mutually exclusive with --sql-file)'),
    ] = None
    db: Annotated[
        str,
        cappa.Arg(default='fba', help='Database schema name'),
    ] = 'fba'
    module: Annotated[
        str | None,
        cappa.Arg(help='Module name within app (defaults to table name)'),
    ] = None
    output: Annotated[
        Path | None,
        cappa.Arg(help='Menu SQL output file path'),
    ] = None
    execute: Annotated[
        bool,
        cappa.Arg(default=False, help='Execute SQL directly to database'),
    ] = False
    parent_menu_id: Annotated[
        int | None,
        cappa.Arg(help='Parent menu ID for nested menus'),
    ] = None

    def __post_init__(self):
        """Validate arguments."""
        if not self.sql_file and not self.table:
            raise cappa.Exit('Either --sql-file or --table must be specified', code=1)
        if self.sql_file and self.table:
            raise cappa.Exit('--sql-file and --table are mutually exclusive', code=1)
        if self.sql_file and not self.sql_file.exists():
            raise cappa.Exit(f'SQL file not found: {self.sql_file}', code=1)

    async def __call__(self) -> None:
        """Execute menu SQL generation."""
        try:
            # Parse SQL or get table info from DB
            if self.sql_file:
                sql_content = self.sql_file.read_text(encoding='utf-8')
                table_info = sql_parser.parse(sql_content)
            else:
                from backend.plugin.code_generator.crud.crud_gen import gen_dao

                async with async_db_session() as db:
                    table_data = await gen_dao.get_table(db, self.db, self.table)
                    if not table_data:
                        raise ValueError(f"Table '{self.table}' not found in schema '{self.db}'")

                    columns_data = await gen_dao.get_all_columns(db, self.db, self.table)
                    table_info = frontend_generator._convert_db_to_table_info(table_data, columns_data)

            # Generate menu SQL
            menu_sql = await generate_menu_sql(
                table_info=table_info,
                app=self.app,
                module=self.module,
                parent_menu_id=self.parent_menu_id,
            )

            # Save to file if output path specified
            if self.output:
                await save_menu_sql_to_file(menu_sql, self.output)
                console.print(f'[green]Menu SQL saved to {self.output}[/green]')

            # Execute if requested
            if self.execute:
                async with async_db_session.begin() as db:
                    await execute_menu_sql(menu_sql, db)
                console.print('[green]Menu SQL executed successfully[/green]')

            # Print SQL if not saving or executing
            if not self.output and not self.execute:
                console.print('\n[cyan]Generated Menu SQL:[/cyan]')
                console.print(menu_sql)

        except Exception as e:
            raise cappa.Exit(e.msg if isinstance(e, BaseExceptionError) else str(e), code=1)


@cappa.command(name='full', help='Generate both frontend code and menu SQL', default_long=True)
@dataclass
class CodegenFull:
    """Generate both frontend CRUD code and menu SQL."""

    app: Annotated[
        str,
        cappa.Arg(help='Application/module name (required)'),
    ]
    sql_file: Annotated[
        Path | None,
        cappa.Arg(help='Path to SQL file (mutually exclusive with --table)'),
    ] = None
    table: Annotated[
        str | None,
        cappa.Arg(help='Table name for DB introspection (mutually exclusive with --sql-file)'),
    ] = None
    db: Annotated[
        str,
        cappa.Arg(default='fba', help='Database schema name'),
    ] = 'fba'
    module: Annotated[
        str | None,
        cappa.Arg(help='Module name within app (defaults to table name)'),
    ] = None
    output_dir: Annotated[
        Path | None,
        cappa.Arg(help='Frontend output directory (auto-detected if omitted)'),
    ] = None
    menu_output: Annotated[
        Path | None,
        cappa.Arg(help='Menu SQL output file path'),
    ] = None
    execute_menu: Annotated[
        bool,
        cappa.Arg(default=False, help='Execute menu SQL directly to database'),
    ] = False
    force: Annotated[
        bool,
        cappa.Arg(default=False, help='Overwrite existing files without prompting'),
    ] = False

    def __post_init__(self):
        """Validate arguments."""
        if not self.sql_file and not self.table:
            raise cappa.Exit('Either --sql-file or --table must be specified', code=1)
        if self.sql_file and self.table:
            raise cappa.Exit('--sql-file and --table are mutually exclusive', code=1)
        if self.sql_file and not self.sql_file.exists():
            raise cappa.Exit(f'SQL file not found: {self.sql_file}', code=1)

    async def __call__(self) -> None:
        """Execute full code generation."""
        try:
            # Generate frontend code
            print('\n\033[1;36mStep 1/2: Generating frontend code\033[0m', flush=True)
            if self.sql_file:
                await frontend_generator.generate_from_sql(
                    sql_file=self.sql_file,
                    app=self.app,
                    module=self.module,
                    output_dir=self.output_dir,
                    force=self.force,
                )
            else:
                async with async_db_session() as db:
                    await frontend_generator.generate_from_db(
                        table=self.table,
                        db_schema=self.db,
                        app=self.app,
                        module=self.module,
                        output_dir=self.output_dir,
                        force=self.force,
                        db=db,
                    )

            # Generate menu SQL
            print('\n\033[1;36mStep 2/2: Generating menu SQL\033[0m', flush=True)

            # Parse SQL or get table info from DB
            if self.sql_file:
                sql_content = self.sql_file.read_text(encoding='utf-8')
                table_info = sql_parser.parse(sql_content)
            else:
                from backend.plugin.code_generator.crud.crud_gen import gen_dao

                async with async_db_session() as db:
                    table_data = await gen_dao.get_table(db, self.db, self.table)
                    columns_data = await gen_dao.get_all_columns(db, self.db, self.table)
                    table_info = frontend_generator._convert_db_to_table_info(table_data, columns_data)

            # Generate menu SQL
            menu_sql = await generate_menu_sql(
                table_info=table_info,
                app=self.app,
                module=self.module,
            )

            # Save to file if output path specified
            if self.menu_output:
                await save_menu_sql_to_file(menu_sql, self.menu_output)
                print(f'\033[32mMenu SQL saved to {self.menu_output}\033[0m', flush=True)

            # Execute if requested
            if self.execute_menu:
                async with async_db_session.begin() as db:
                    await execute_menu_sql(menu_sql, db)
                print('\033[32mMenu SQL executed successfully\033[0m', flush=True)

            print('\n\033[1;32m✓ Full code generation completed!\033[0m', flush=True)

        except Exception as e:
            raise cappa.Exit(e.msg if isinstance(e, BaseExceptionError) else str(e), code=1)
