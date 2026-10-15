"""Bounded JSON-line desktop interaction; temporary user tokens never cross this pipe."""

import asyncio
import json
import sys
from uuid import uuid4

from atellagent_client.onboarding.contract_values import canonical_id
from atellagent_client.onboarding.errors import OnboardingClientError


class SetupInteraction:
    def __init__(self, *, reader=None, writer=None):
        self.reader = sys.stdin if reader is None else reader
        self.writer = sys.stdout if writer is None else writer

    def emit(self, event, **fields):
        payload = json.dumps(dict(event=event, **fields), ensure_ascii=True, sort_keys=True)
        if len(payload.encode("utf-8")) > 65536:
            raise OnboardingClientError("onboarding_interaction_too_large")
        self.writer.write(payload + "\n")
        self.writer.flush()

    async def ask(self, event, **fields):
        prompt_id = str(uuid4())
        self.emit(event, prompt_id=prompt_id, **fields)
        line = await asyncio.to_thread(self.reader.readline, 8193)
        try:
            if not line.endswith("\n") or len(line.encode("utf-8")) > 8192:
                raise ValueError("bounded response required")
            def closed_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate response field")
                    result[key] = value
                return result
            response = json.loads(line, object_pairs_hook=closed_pairs)
            if not isinstance(response, dict) or set(response) != {"prompt_id", "value"}:
                raise ValueError("closed response required")
            if response["prompt_id"] != prompt_id:
                raise ValueError("response does not belong to current prompt")
            return response["value"]
        except (ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_interaction_invalid") from exc

    def sign_in(self, challenge):
        self.emit("sign_in", browser_url=challenge.browser_url, user_code=challenge.user_code)

    async def confirm_mcp(self, review):
        value = await self.ask("review_mcp_target", review=review)
        if (not isinstance(value, dict) or set(value) != {"request_id", "local_inputs_sha256", "approved"}
                or value["request_id"] != review["request_id"]
                or value["local_inputs_sha256"] != review["local_inputs_sha256"]
                or type(value["approved"]) is not bool):
            raise OnboardingClientError("onboarding_mcp_local_review_changed")
        return value["approved"]

    async def choose_membership(self, identity):
        value = await self.ask("choose_organization", user_id=str(identity.user_id),
            organizations=[dict(tenant_id=str(row.tenant_id), name=row.tenant_name)
                           for row in identity.memberships])
        try:
            tenant = canonical_id(value)
            identity.require_selection(expected_user_id=identity.user_id, tenant_id=tenant)
        except (ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_organization_selection_invalid") from exc
        return identity.user_id, tenant

    async def confirm_native(self, review):
        value = await self.ask("review_connection", review=review)
        if (not isinstance(value, dict) or set(value) != {"request_id", "intent_sha256", "approved"}
                or value["request_id"] != review["request_id"]
                or value["intent_sha256"] != review["intent_sha256"]
                or type(value["approved"]) is not bool):
            raise OnboardingClientError("onboarding_native_consent_intent_changed")
        return value["approved"]
