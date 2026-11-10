# Local tool adapters

## Purpose

`PostgresTools` is an optional customer-owned PostgreSQL tool adapter for a
connected handler. Install `atellagent-client[postgres]` and keep the database
DSN in the customer environment rather than in generated configuration.

## Public entry points and coverage

Use `PostgresTools` and `PostgresWriteOperation`. The adapter validates its
documented single-statement read/write shape; surround each consequential call
with the appropriate connected or local action boundary. The adapter itself is
not a model-governance mode, an identity resolver, or an authorization layer for
database access that bypasses it.

```python
from atellagent_client.integrations.tools import PostgresTools

tools = PostgresTools.from_environment()
```

Connection, statement, and database failures surface to the caller. Do not log
the DSN or query payloads in support requests.
