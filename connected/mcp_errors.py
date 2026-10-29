"""Actionable local target failures without remote payloads or credentials."""


class MCPTargetError(ValueError):
    def __init__(self, code, message):
        if code not in {"mcp_target_protocol_unsupported", "mcp_target_discovery_invalid",
                        "mcp_target_idempotency_required", "mcp_target_transport_unsupported",
                        "mcp_target_dependency_missing"}:
            raise ValueError("closed MCP target failure code required")
        self.code = code
        super().__init__(message)
