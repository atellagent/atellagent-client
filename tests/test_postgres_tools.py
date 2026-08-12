# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free behavior checks for the bounded PostgreSQL MCP tools."""

from __future__ import annotations

import asyncio
import unittest

from atellagent_client.integrations.tools import PostgresTools, PostgresWriteOperation


class _Column:
    def __init__(self, name: str) -> None:
        self.name = name


class _Cursor:
    def __init__(self, rows=(), row_count=0) -> None:
        self.description = [_Column("id"), _Column("created_at")]
        self.rows = list(rows)
        self.rowcount = row_count
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))

    async def fetchmany(self, _size):
        return self.rows


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        self.closed = True


class PostgresToolsTests(unittest.TestCase):
    def test_query_is_parameterized_read_only_and_bounded(self) -> None:
        async def run() -> None:
            cursor = _Cursor(rows=[(1, "2026-08-04"), (2, "2026-08-05"), (3, "2026-08-06")])
            connection = _Connection(cursor)
            received_dsn = []

            async def connect(dsn):
                received_dsn.append(dsn)
                return connection

            tools = PostgresTools("postgresql://db.example.test/support", max_rows=2, connection_factory=connect)
            result = await tools.query("SELECT id, created_at FROM tickets WHERE tenant_id = %(tenant)s", {"tenant": "t-1"})

            self.assertEqual(received_dsn, ["postgresql://db.example.test/support"])
            self.assertEqual(cursor.calls[0], ("SET TRANSACTION READ ONLY", None))
            self.assertEqual(cursor.calls[1][1], {"tenant": "t-1"})
            self.assertEqual(result["row_count"], 2)
            self.assertTrue(result["truncated"])
            self.assertEqual(result["rows"][0]["id"], 1)
            self.assertTrue(connection.rolled_back)
            self.assertFalse(connection.committed)
            self.assertTrue(connection.closed)

        asyncio.run(run())

    def test_query_rejects_non_select_and_multiple_statements_before_connecting(self) -> None:
        async def connect(_dsn):
            raise AssertionError("connection must not be opened")

        tools = PostgresTools("postgresql://db.example.test/support", connection_factory=connect)
        with self.assertRaisesRegex(ValueError, "begin with SELECT"):
            asyncio.run(tools.query("UPDATE tickets SET state = 'closed'"))
        with self.assertRaisesRegex(ValueError, "one non-empty statement"):
            asyncio.run(tools.query("SELECT 1; SELECT 2"))

    def test_write_uses_only_customer_declared_template(self) -> None:
        async def run() -> None:
            cursor = _Cursor(row_count=1)
            connection = _Connection(cursor)
            tools = PostgresTools(
                "postgresql://db.example.test/support",
                write_operations=(
                    PostgresWriteOperation(
                        "set_ticket_status",
                        "UPDATE tickets SET status = %(status)s WHERE id = %(ticket_id)s",
                        "Set a ticket's status.",
                    ),
                ),
                connection_factory=lambda _dsn: connection,
            )

            result = await tools.write("set_ticket_status", {"ticket_id": 7, "status": "closed"})
            self.assertEqual(result, {"operation": "set_ticket_status", "row_count": 1})
            self.assertEqual(cursor.calls, [("UPDATE tickets SET status = %(status)s WHERE id = %(ticket_id)s", {"ticket_id": 7, "status": "closed"})])
            self.assertTrue(connection.committed)
            self.assertTrue(connection.closed)
            with self.assertRaisesRegex(ValueError, "not declared"):
                await tools.write("run_arbitrary_sql", {})

            metadata = tools.tool_metadata()
            self.assertEqual([entry["name"] for entry in metadata], ["postgres_query", "postgres_write_set_ticket_status"])
            self.assertNotIn("db.example.test", str(metadata))
            self.assertNotIn("UPDATE tickets", str(metadata))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
