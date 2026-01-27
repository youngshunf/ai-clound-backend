"""SQL Parser for extracting table metadata from CREATE TABLE statements."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class DatabaseDialect(str, Enum):
    """Database dialect enumeration."""

    MYSQL = 'mysql'
    POSTGRESQL = 'postgresql'


@dataclass
class ColumnInfo:
    """Column information extracted from SQL."""

    name: str
    type: str  # Raw SQL type (e.g., "VARCHAR", "INTEGER")
    length: Optional[int] = None
    nullable: bool = True
    default: Optional[str] = None
    comment: Optional[str] = None
    is_primary_key: bool = False
    is_auto_increment: bool = False


@dataclass
class TableInfo:
    """Table information extracted from SQL."""

    name: str
    comment: Optional[str] = None
    columns: list[ColumnInfo] = None
    dialect: DatabaseDialect = DatabaseDialect.POSTGRESQL

    def __post_init__(self):
        if self.columns is None:
            self.columns = []


class SQLParser:
    """Parser for CREATE TABLE statements."""

    # Regex patterns for parsing
    MYSQL_COMMENT_PATTERN = re.compile(r"COMMENT\s+'([^']*)'", re.IGNORECASE)
    PG_COMMENT_ON_TABLE = re.compile(
        r"COMMENT\s+ON\s+TABLE\s+(\w+)\s+IS\s+'([^']*)';?", re.IGNORECASE
    )
    PG_COMMENT_ON_COLUMN = re.compile(
        r"COMMENT\s+ON\s+COLUMN\s+(\w+)\.(\w+)\s+IS\s+'([^']*)';?", re.IGNORECASE
    )

    def parse(self, sql: str) -> TableInfo:
        """
        Parse CREATE TABLE statement.

        :param sql: SQL CREATE TABLE statement
        :return: TableInfo object
        """
        # Detect dialect
        dialect = self._detect_dialect(sql)

        # Extract table name and comment
        table_name = self._extract_table_name(sql)
        table_comment = self._extract_table_comment(sql, table_name, dialect)

        # Parse column definitions
        columns = self._parse_columns(sql, table_name, dialect)

        return TableInfo(name=table_name, comment=table_comment, columns=columns, dialect=dialect)

    def _detect_dialect(self, sql: str) -> DatabaseDialect:
        """
        Detect database dialect from SQL syntax.

        :param sql: SQL statement
        :return: DatabaseDialect
        """
        sql_upper = sql.upper()

        # MySQL indicators
        mysql_indicators = [
            'AUTO_INCREMENT',
            'ENGINE=',
            'CHARSET=',
            'COLLATE=',
            '`',  # Backticks
        ]

        # PostgreSQL indicators
        pg_indicators = [
            'SERIAL',
            'BIGSERIAL',
            'SMALLSERIAL',
            'COMMENT ON COLUMN',
            'COMMENT ON TABLE',
            '::',  # Type casting
        ]

        mysql_score = sum(1 for indicator in mysql_indicators if indicator in sql_upper)
        pg_score = sum(1 for indicator in pg_indicators if indicator in sql_upper)

        return DatabaseDialect.MYSQL if mysql_score > pg_score else DatabaseDialect.POSTGRESQL

    def _extract_table_name(self, sql: str) -> str:
        """
        Extract table name from CREATE TABLE statement.

        :param sql: SQL statement
        :return: Table name
        """
        # Match: CREATE TABLE [IF NOT EXISTS] ["schema".]"table_name" or schema.table_name
        # Handle quoted identifiers for PostgreSQL
        pattern = re.compile(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`"]?\w+[`"]?\.)?[`"]?(\w+)[`"]?',
            re.IGNORECASE
        )
        match = pattern.search(sql)
        if not match:
            raise ValueError('Could not extract table name from SQL')
        return match.group(1)

    def _extract_table_comment(self, sql: str, table_name: str, dialect: DatabaseDialect) -> Optional[str]:
        """
        Extract table comment.

        :param sql: SQL statement
        :param table_name: Table name
        :param dialect: Database dialect
        :return: Table comment or None
        """
        if dialect == DatabaseDialect.MYSQL:
            # MySQL: COMMENT='...' at end of CREATE TABLE
            pattern = re.compile(r"COMMENT\s*=\s*'([^']*)'", re.IGNORECASE)
            match = pattern.search(sql)
            return match.group(1) if match else None
        else:
            # PostgreSQL: COMMENT ON TABLE ... IS '...'
            match = self.PG_COMMENT_ON_TABLE.search(sql)
            if match and match.group(1).lower() == table_name.lower():
                return match.group(2)
            return None

    def _parse_columns(self, sql: str, table_name: str, dialect: DatabaseDialect) -> list[ColumnInfo]:
        """
        Parse column definitions from CREATE TABLE statement.

        :param sql: SQL statement
        :param table_name: Table name
        :param dialect: Database dialect
        :return: List of ColumnInfo objects
        """
        # Extract the column definitions section
        # Find content between first ( and last )
        start_idx = sql.find('(')
        end_idx = sql.rfind(')')
        if start_idx == -1 or end_idx == -1:
            return []

        columns_section = sql[start_idx + 1 : end_idx]

        # Split by comma, but be careful with nested parentheses
        column_defs = self._split_column_definitions(columns_section)

        columns = []
        primary_keys = set()

        # First pass: identify primary keys from constraints
        for col_def in column_defs:
            col_def_stripped = col_def.strip()
            if col_def_stripped.upper().startswith('PRIMARY KEY'):
                # Extract column names from PRIMARY KEY (col1, col2, ...)
                pk_match = re.search(r'PRIMARY\s+KEY\s*\(([^)]+)\)', col_def_stripped, re.IGNORECASE)
                if pk_match:
                    pk_cols = [c.strip().strip('`"') for c in pk_match.group(1).split(',')]
                    primary_keys.update(pk_cols)

        # Second pass: parse column definitions
        for col_def in column_defs:
            col_def_stripped = col_def.strip()

            # Skip constraint definitions
            if any(
                col_def_stripped.upper().startswith(kw)
                for kw in ['PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE', 'CHECK', 'CONSTRAINT', 'INDEX', 'KEY']
            ):
                continue

            # Parse column
            column = self._parse_column_definition(col_def_stripped, dialect)
            if column:
                # Check if this column is a primary key
                if column.name in primary_keys:
                    column.is_primary_key = True
                columns.append(column)

        # Third pass: extract PostgreSQL column comments
        if dialect == DatabaseDialect.POSTGRESQL:
            for match in self.PG_COMMENT_ON_COLUMN.finditer(sql):
                tbl_name, col_name, comment = match.groups()
                if tbl_name.lower() == table_name.lower():
                    for column in columns:
                        if column.name.lower() == col_name.lower():
                            column.comment = comment
                            break

        return columns

    def _split_column_definitions(self, columns_section: str) -> list[str]:
        """
        Split column definitions by comma, handling nested parentheses.

        :param columns_section: Column definitions section
        :return: List of column definition strings
        """
        result = []
        current = []
        paren_depth = 0

        for char in columns_section:
            if char == '(':
                paren_depth += 1
                current.append(char)
            elif char == ')':
                paren_depth -= 1
                current.append(char)
            elif char == ',' and paren_depth == 0:
                result.append(''.join(current))
                current = []
            else:
                current.append(char)

        if current:
            result.append(''.join(current))

        return result

    def _parse_column_definition(self, col_def: str, dialect: DatabaseDialect) -> Optional[ColumnInfo]:
        """
        Parse a single column definition.

        :param col_def: Column definition string
        :param dialect: Database dialect
        :return: ColumnInfo object or None
        """
        # Remove leading/trailing whitespace
        col_def = col_def.strip()

        # Extract column name (first word, possibly quoted)
        name_match = re.match(r'^[`"]?(\w+)[`"]?\s+', col_def)
        if not name_match:
            return None

        column_name = name_match.group(1)
        remaining = col_def[name_match.end() :].strip()

        # Extract data type
        type_match = re.match(r'^(\w+)(?:\s*\(([^)]+)\))?', remaining, re.IGNORECASE)
        if not type_match:
            return None

        column_type = type_match.group(1).upper()
        type_params = type_match.group(2)
        remaining = remaining[type_match.end() :].strip()

        # Parse length/precision
        length = None
        if type_params:
            # Extract first number as length
            length_match = re.match(r'(\d+)', type_params)
            if length_match:
                length = int(length_match.group(1))

        # Initialize column info
        column = ColumnInfo(name=column_name, type=column_type, length=length)

        # Parse constraints and attributes
        remaining_upper = remaining.upper()

        # Check for PRIMARY KEY
        if 'PRIMARY KEY' in remaining_upper or column_type in ('SERIAL', 'BIGSERIAL', 'SMALLSERIAL'):
            column.is_primary_key = True
            column.nullable = False

        # Check for AUTO_INCREMENT
        if 'AUTO_INCREMENT' in remaining_upper or column_type in ('SERIAL', 'BIGSERIAL', 'SMALLSERIAL'):
            column.is_auto_increment = True

        # Check for NOT NULL
        if 'NOT NULL' in remaining_upper:
            column.nullable = False
        elif 'NULL' in remaining_upper and 'NOT NULL' not in remaining_upper:
            column.nullable = True

        # Extract DEFAULT value
        default_match = re.search(r'DEFAULT\s+([^\s,]+(?:\s+[^\s,]+)*?)(?:\s+(?:COMMENT|NOT|NULL|,|$))', remaining, re.IGNORECASE)
        if default_match:
            column.default = default_match.group(1).strip()

        # Extract MySQL inline comment
        if dialect == DatabaseDialect.MYSQL:
            comment_match = self.MYSQL_COMMENT_PATTERN.search(remaining)
            if comment_match:
                column.comment = comment_match.group(1)

        return column


# Singleton instance
sql_parser = SQLParser()
