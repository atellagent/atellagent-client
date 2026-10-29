# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""A deliberately bounded PostgreSQL tool integration.

The adapter keeps the connection string and write SQL in the customer-hosted
process.  A caller can supply parameter *values*, but it cannot supply a write
statement: writes select a customer-declared named template.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence, Union
from uuid import UUID


PostgresParameters = Union[Mapping[str, Any], Sequence[Any], None]
ConnectionFactory = Callable[[str], Union[Any, Awaitable[Any]]]


@dataclass(frozen=True)
class PostgresWriteOperation:
    """A customer-declared, single-statement write operation."""

    name: str
    statement: str
    description: str = ""


def _single_statement(statement: str, *, kind: str) -> str:
    normalized = str(statement or "").strip()
    if not normalized or ";" in normalized or "\x00" in normalized:
        raise ValueError(f"PostgreSQL {kind} must be one non-empty statement")
    return normalized


def _read_statement(statement: str) -> str:
    normalized = _single_statement(statement, kind="query")
    # CTEs and EXPLAIN can contain data-modifying statements.  A narrow SELECT
    # surface is intentional: use a database view for any more complex read.
    if not normalized.lower().startswith("select"):
        raise ValueError("PostgreSQL query must begin with SELECT")
    return normalized


def _write_statement(statement: str) -> str:
    normalized = _single_statement(statement, kind="write operation")
    if not normalized.lower().startswith(("insert", "update", "delete")):
        raise ValueError("PostgreSQL write operations must begin with INSERT, UPDATE, or DELETE")
    return normalized


def _operation_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("PostgreSQL write operation names must be alphanumeric, hyphen, or underscore")
    return name


def _parameters(value: PostgresParameters) -> PostgresParameters:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) and key for key in value):
            raise ValueError("PostgreSQL parameter mapping keys must be non-empty strings")
        return dict(value)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("PostgreSQL parameters must be a mapping or sequence")
    return list(value)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


class PostgresTools:
    """Expose bounded PostgreSQL query and declared-write MCP tools.

    Use a read-only database role when no ``write_operations`` are configured.
    Even for declared writes, grant the integration role only the table and
    operation privileges it needs.  This adapter is a tool boundary, not a SQL
    authorization system.
    """

    def __init__(
        self,
        dsn: str,
        *,
        write_operations: Sequence[PostgresWriteOperation] = (),
        max_rows: int = 100,
        connection_factory: Optional[ConnectionFactory] = None,
    ) -> None:
        self._dsn = str(dsn or "").strip()
        if not self._dsn:
            raise ValueError("PostgreSQL DSN is required")
        if not isinstance(max_rows, int) or not 1 <= max_rows <= 10_000:
            raise ValueError("max_rows must be an integer between 1 and 10000")
        self._max_rows = max_rows
        self._connection_factory = connection_factory or self._default_connection_factory
        operations: Dict[str, PostgresWriteOperation] = {}
        for raw_operation in write_operations:
            if not isinstance(raw_operation, PostgresWriteOperation):
                raise ValueError("write_operations must contain PostgresWriteOperation values")
            name = _operation_name(raw_operation.name)
            if name in operations:
                raise ValueError(f"duplicate PostgreSQL write operation: {name}")
            operations[name] = PostgresWriteOperation(
                name=name,
                statement=_write_statement(raw_operation.statement),
                description=str(raw_operation.description or "").strip(),
            )
        self._write_operations = operations

    @classmethod
    def from_environment(
        cls,
        *,
        dsn_environment_variable: str = "POSTGRES_DSN",
        **kwargs: Any,
    ) -> "PostgresTools":
        """Create an adapter from a local environment variable, never a request."""
        variable = str(dsn_environment_variable or "").strip()
        if not variable:
            raise ValueError("dsn_environment_variable is required")
        return cls(os.environ.get(variable, ""), **kwargs)

    @staticmethod
    async def _default_connection_factory(dsn: str) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - exercised by install docs
            raise RuntimeError(
                "psycopg is not installed. Run: pip install 'atellagent-client[postgres]'"
            ) from exc
        return await psycopg.AsyncConnection.connect(dsn)

    async def _connection(self) -> Any:
        connection = self._connection_factory(self._dsn)
        if inspect.isawaitable(connection):
            connection = await connection
        if connection is None or not hasattr(connection, "cursor"):
            raise RuntimeError("PostgreSQL connection factory returned an invalid connection")
        return connection

    @staticmethod
    async def _close(connection: Any, *, rollback: bool) -> None:
        try:
            if rollback and hasattr(connection, "rollback"):
                result = connection.rollback()
                if inspect.isawaitable(result):
                    await result
        finally:
            if hasattr(connection, "close"):
                result = connection.close()
                if inspect.isawaitable(result):
                    await result

    async def query(
        self,
        statement: str,
        parameters: PostgresParameters = None,
    ) -> Dict[str, Any]:
        """Run a parameterized SELECT and return no more than ``max_rows`` rows."""
        statement = _read_statement(statement)
        parameters = _parameters(parameters)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                # This transaction-level setting is defense in depth. A
                # least-privilege, read-only database role remains required.
                await cursor.execute("SET TRANSACTION READ ONLY")
                await cursor.execute(statement, parameters)
                description = getattr(cursor, "description", None) or ()
                columns = [str(column.name) for column in description]
                fetched = await cursor.fetchmany(self._max_rows + 1)
                truncated = len(fetched) > self._max_rows
                rows = fetched[: self._max_rows]
                return {
                    "columns": columns,
                    "rows": [
                        {
                            column: _json_value(value)
                            for column, value in zip(columns, row, strict=True)
                        }
                        for row in rows
                    ],
                    "row_count": len(rows),
                    "truncated": truncated,
                }
        except Exception as exc:
            raise RuntimeError("PostgreSQL query failed") from exc
        finally:
            await self._close(connection, rollback=True)

    async def write(
        self,
        operation: str,
        parameters: PostgresParameters = None,
    ) -> Dict[str, Any]:
        """Run a customer-declared write template selected by name."""
        name = _operation_name(operation)
        configured = self._write_operations.get(name)
        if configured is None:
            raise ValueError("PostgreSQL write operation is not declared")
        parameters = _parameters(parameters)
        connection = await self._connection()
        committed = False
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(configured.statement, parameters)
                row_count = int(getattr(cursor, "rowcount", 0) or 0)
            result = connection.commit()
            if inspect.isawaitable(result):
                await result
            committed = True
            return {"operation": name, "row_count": max(0, row_count)}
        except Exception as exc:
            raise RuntimeError("PostgreSQL write operation failed") from exc
        finally:
            await self._close(connection, rollback=not committed)

    def tool_metadata(self) -> list[Dict[str, Any]]:
        """Return matching tool metadata without database details."""
        metadata: list[Dict[str, Any]] = [
            {
                "name": "postgres_query",
                "description": "Run a bounded, parameterized SELECT query.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string"},
                        "parameters": {"type": ["object", "array", "null"]},
                    },
                    "required": ["statement"],
                },
            }
        ]
        for operation in self._write_operations.values():
            metadata.append(
                {
                    "name": f"postgres_write_{operation.name}",
                    "description": operation.description or f"Run declared PostgreSQL operation {operation.name}.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"parameters": {"type": ["object", "array", "null"]}},
                    },
                }
            )
        return metadata

__all__ = ["PostgresTools", "PostgresWriteOperation"]
