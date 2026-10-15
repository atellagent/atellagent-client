"""Reuse ordinary SDK event operations with the participant's enrolled identity."""

from atellagent_client.sdk.client_modules.agent_events import AgentEventsClientMixin
from atellagent_client.sdk.client_modules.base import ClientBaseMixin
from atellagent_client.sdk.operations import APIOperations


class ConnectedAgentEvents(AgentEventsClientMixin, ClientBaseMixin):
    def __init__(self, context):
        self.service_account_config = context.config
        self.gateway_session = context.session
        self.telemetry_context = {
            'service_account_id': context.config.service_account_id,
            'auth_client_id': context.config.auth_client_id,
        }
        self.operations = APIOperations(context.session.base_url,
                                        api_version=context.config.api_version)
