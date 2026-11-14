# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# Licensed under LICENSE.md in this source tree.
"""Original-tool MCP facade over an enrolled participant's governed intake."""

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from typing import Mapping, Any

from .agent import MCPAgentProxy, MCPAgentProxyError, _header, _request_id
from .native_credentials import bind_native_authorization


@dataclass(frozen=True, repr=False)
class NativeMCPContext:
    """Created by the listener AFTER native authentication and authorization.

    This value does not verify a token. Never construct it from tool arguments
    or unchecked headers. The logical action key is independent of JSON-RPC IDs.
    """
    caller: Mapping[str, Any]
    action_key: str
    authorized_tools: frozenset[str]
    upstream_authorization: str | None = None
    _caller_json: bytes = field(init=False)

    def __post_init__(self):
        if (not isinstance(self.caller, Mapping) or set(self.caller)-{"issuer","subject","account_id","attributes"}
                or any(not isinstance(self.caller.get(k),str) or not self.caller[k]
                       or self.caller[k]!=self.caller[k].strip() for k in ("issuer","subject"))
                or not isinstance(self.action_key,str) or not 1<=len(self.action_key)<=255
                or self.action_key!=self.action_key.strip()
                or not isinstance(self.authorized_tools,frozenset)
                or any(not isinstance(n,str) or not n or n!=n.strip() for n in self.authorized_tools)):
            raise ValueError("Verified native caller, authorized tools and logical action key required")
        with bind_native_authorization(self.upstream_authorization):
            pass
        encoded=json.dumps(dict(self.caller),sort_keys=True,separators=(",",":"),allow_nan=False).encode()
        if len(encoded)>8192: raise ValueError("Native caller summary is too large")
        object.__setattr__(self,"_caller_json",encoded)

    def caller_payload(self):
        return json.loads(self._caller_json)

    def session_identity(self):
        caller=self.caller_payload()
        return sha256(json.dumps({k:caller.get(k) for k in ("issuer","subject","account_id")},
            sort_keys=True,separators=(",",":")).encode()).hexdigest()


@dataclass(frozen=True)
class _PublishedTool:
    value: dict
    def as_mcp_tool(self): return json.loads(json.dumps(self.value))


@dataclass(frozen=True)
class _ReleasedResult:
    value: dict
    def as_mcp_result(self):
        # Protocol framing is rendered by the common facade. Tool data and all
        # original structured/media content remain intact after governed release.
        return {k:v for k,v in self.value.items() if k!="resultType"}


class _ResourceInvocationPort:
    def __init__(self, participant):
        self.participant=participant
        self.context=ContextVar("mcp_native_request_context",default=None)

    async def list_tools(self):
        context=self.context.get()
        participant=self.participant
        if (context is None or not participant._started or participant._mcp_publication is None
                or not isinstance(participant._mcp_manifest,dict)):
            raise MCPAgentProxyError("The enrolled MCP runtime is unavailable")
        catalog=json.loads(json.dumps(participant._mcp_manifest,allow_nan=False))
        return tuple(_PublishedTool(tool) for tool in catalog.get("tools",[])
                     if tool.get("name") in context.authorized_tools)

    async def invoke_tool(self, *, tool_name, arguments, peer_call_id):
        context=self.context.get()
        if context is None or tool_name not in context.authorized_tools:
            raise MCPAgentProxyError("Native authorization does not permit this tool")
        reply=await self.participant.handle_mcp_request({"jsonrpc":"2.0","id":peer_call_id,
            "method":"tools/call","params":{"name":tool_name,"arguments":dict(arguments)}},
            idempotency_key=context.action_key,caller=context.caller_payload())
        if (not isinstance(reply,dict) or set(reply)!={"jsonrpc","id","result"}
                or reply["jsonrpc"]!="2.0" or type(reply["id"]) is not type(peer_call_id)
                or reply["id"]!=peer_call_id or not isinstance(reply["result"],dict)):
            raise MCPAgentProxyError("The MCP runtime did not release a complete result")
        return _ReleasedResult(reply["result"])


class MCPResourceProxy(MCPAgentProxy):
    """Mount in the existing authenticated HTTP listener; no runtime or login.

    Keep the unchanged upstream on stdio, a private socket or loopback. A
    publicly reachable upstream remains a bypass regardless of this facade.
    """
    def __init__(self, *, participant, authorization_boundary, session_ttl_seconds=900, max_sessions=1024):
        if authorization_boundary not in {"native_listener","native_upstream"}:
            raise ValueError("Select the existing native authorization boundary")
        self._authorization_boundary=authorization_boundary
        self._resource_port=_ResourceInvocationPort(participant)
        super().__init__(gateway=self._resource_port,session_ttl_seconds=session_ttl_seconds,max_sessions=max_sessions)

    async def handle(self, document, *, context: NativeMCPContext, headers=None):
        if type(context) is not NativeMCPContext:
            raise MCPAgentProxyError("An authenticated native request context is required")
        if self._authorization_boundary=="native_upstream" and context.upstream_authorization is None:
            raise MCPAgentProxyError("The original upstream native authorization is required")
        try:
            encoded=json.dumps(dict(document),allow_nan=False).encode()
            if len(encoded)>1024*1024: raise ValueError()
            prepared=json.loads(encoded)
        except (TypeError,ValueError):
            raise MCPAgentProxyError("MCP request is not a bounded JSON object") from None
        if prepared.get("method")=="tools/call":
            if _request_id(prepared) is None:
                raise MCPAgentProxyError("A tool call requires a response correlation")
            params=prepared.get("params")
            if not isinstance(params,dict): raise MCPAgentProxyError("Tool parameters must be an object")
            metadata=params.get("_meta",{})
            if not isinstance(metadata,dict) or any(str(k).lower().startswith("atellagent/") for k in metadata):
                raise MCPAgentProxyError("Caller metadata cannot supply execution authority")
        session_id=_header(headers,"mcp-session-id")
        if session_id:
            session=self._sessions.get(session_id)
            if session.caller_identity!=context.session_identity():
                raise MCPAgentProxyError("MCP session belongs to another native caller")
        token=self._resource_port.context.set(context)
        try:
            with bind_native_authorization(context.upstream_authorization):
                response=await super().handle(prepared,headers=headers)
            if prepared.get("method")=="initialize" and response.session_id:
                session=self._sessions.get(response.session_id)
                self._sessions._sessions[response.session_id]=replace(session,caller_identity=context.session_identity())
            return response
        finally:
            self._resource_port.context.reset(token)
