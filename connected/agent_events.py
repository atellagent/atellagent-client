"""Keep connected observations bound to their current delivery authority."""

from uuid import UUID

from atellagent_client.protocol.api import build_versioned_route
from atellagent_client.sdk.client_modules.agent_events import AgentEventsClientMixin
from atellagent_client.sdk.client_modules.base import ClientBaseMixin
from atellagent_client.sdk.operations import APIOperations

from .contracts import ConnectedProtocolError


class ConnectedAgentEvents(AgentEventsClientMixin, ClientBaseMixin):
    def __init__(self, context):
        self._event_context = context
        self.service_account_config = context.config
        self.gateway_session = context.session
        self.telemetry_context = {
            "service_account_id": context.config.service_account_id,
            "auth_client_id": context.config.auth_client_id,
        }
        self.operations = APIOperations(
            context.session.base_url, api_version=context.config.api_version
        )

    def _event_transport(self, method, url):
        context = self._event_context
        ordinary_url = context.session.base_url + build_versioned_route(
            context.config.api_version, "/agent-events/ingest"
        )
        if method != "POST" or url != ordinary_url:
            raise ConnectedProtocolError(
                "Agent event request is not bound to this delivery"
            )
        try:
            instance = UUID(str(context.instance_id))
            message = UUID(str(context.message.message_id))
            lease = UUID(str(context.message.lease.lease_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ConnectedProtocolError(
                "Agent event delivery identity is invalid"
            ) from exc
        if not isinstance(context.capability, str) or not context.capability.strip():
            raise ConnectedProtocolError(
                "Agent event delivery capability is unavailable"
            )
        path = (
            f"/connected-runtimes/instances/{instance}/messages/{message}"
            f"/leases/{lease}/actions/event"
        )
        return (
            context.session.base_url
            + build_versioned_route(context.config.api_version, path),
            {"X-Atellagent-Connected-Capability": context.capability},
        )

    @staticmethod
    def _require_event_http2(response):
        if response.http_version != "HTTP/2":
            raise ConnectedProtocolError("Connected agent event did not use HTTP/2")
        return response

    async def _request_gateway_async(self, method, url, *, headers=None, **kwargs):
        target, authority_headers = self._event_transport(method, url)
        response = await self.gateway_session.request_authenticated(
            method, target, headers=authority_headers, **kwargs
        )
        return self._require_event_http2(response)

    def _request_gateway_sync(self, method, url, *, headers=None, **kwargs):
        target, authority_headers = self._event_transport(method, url)
        response = self.gateway_session.request_authenticated_sync(
            method, target, headers=authority_headers, **kwargs
        )
        return self._require_event_http2(response)
