# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Credential-free checks for the public Slack channel adapter."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from atellagent_client.integrations.channels.slack import SlackChannelAdapter


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {
            "ok": True,
            "channel": "C123",
            "ts": "1700000000.000001",
            "message": {"thread_ts": "1700000000.000000"},
        }


class _AsyncClient:
    request = None

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url, *, headers, json):
        type(self).request = {"url": url, "headers": headers, "json": json}
        return _Response()


class SlackChannelAdapterTests(unittest.TestCase):
    signing_secret = "test-signing-secret"

    def _signed_headers(self, raw_body: bytes):
        timestamp = str(int(time.time()))
        base = f"v0:{timestamp}:".encode("utf-8") + raw_body
        signature = "v0=" + hmac.new(
            self.signing_secret.encode("utf-8"), base, hashlib.sha256
        ).hexdigest()
        return {
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        }

    def test_signed_event_callback_preserves_thread_routing(self) -> None:
        payload = {
            "type": "event_callback",
            "event_id": "Ev123",
            "team_id": "T123",
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C123",
                "text": "hello",
                "ts": "1700000000.000000",
                "thread_ts": "1700000000.000000",
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        adapter = SlackChannelAdapter(signing_secret=self.signing_secret)

        submission = adapter.normalize_ingress_event(
            {},
            raw_body=raw_body,
            headers=self._signed_headers(raw_body),
            content_type="application/json",
        )

        self.assertEqual(submission.idempotency_key, "Ev123")
        self.assertEqual(submission.input_data["channel_id"], "C123")
        self.assertEqual(
            submission.input_data["channel_thread_id"], "slack:C123:1700000000.000000"
        )
        self.assertEqual(submission.event["actor"]["user_id"], "U123")

    def test_egress_posts_only_the_declared_slack_message_action(self) -> None:
        adapter = SlackChannelAdapter(bot_token="xoxb-test-token")
        with patch(
            "atellagent_client.integrations.channels.slack.httpx.AsyncClient",
            _AsyncClient,
        ):
            result = asyncio.run(
                adapter.dispatch_egress_action(
                    action="send_message",
                    payload={"channel": "C123", "thread_ts": "1700000000.000000", "text": "done"},
                    metadata={},
                    envelope={"event_id": "effect-1"},
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["channel"], "C123")
        self.assertEqual(_AsyncClient.request["url"], "https://slack.com/api/chat.postMessage")
        self.assertEqual(_AsyncClient.request["json"]["text"], "done")
        self.assertEqual(
            _AsyncClient.request["json"]["client_msg_id"],
            str(uuid5(NAMESPACE_URL, "atellagent:slack:effect-1")),
        )
        self.assertEqual(
            _AsyncClient.request["headers"]["Authorization"], "Bearer xoxb-test-token"
        )


if __name__ == "__main__":
    unittest.main()
