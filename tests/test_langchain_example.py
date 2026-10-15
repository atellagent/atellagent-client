# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Optional framework wiring tests; live acceptance is recorded separately."""

import importlib.util
import unittest

from atellagent_client.sdk import AtellagentClient, PolicyViolationError

HAS_LANGCHAIN = importlib.util.find_spec("langchain") is not None

if HAS_LANGCHAIN:
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from atellagent_client.examples.agent.langchain_model import ExampleChatModel, request_messages, response_message
    from atellagent_client.examples.agent.langchain_agent_quickstart import make_echo_tool


class RecordingClient(AtellagentClient):
    def __init__(self, failure=None):
        self.failure = failure
        self.models = []
        self.tools = []

    async def invoke_model_async(self, **kwargs):
        self.models.append(kwargs)
        if self.failure == "model":
            raise ConnectionError("Required model transport unavailable")
        if self.failure == "model_deny":
            raise PolicyViolationError("Model denied", "test_deny")
        calls = [{"id":"call-1", "name":"governed_echo", "arguments":{"text":"hello"}}] if len(self.models) == 1 else []
        return {"output_text":"" if calls else "Completed", "tool_requests":calls}

    async def call_mcp_tool_result_async(self, target, name, arguments, **kwargs):
        self.tools.append((target, name, arguments, kwargs))
        if self.failure == "tool":
            raise PolicyViolationError("Tool denied", "test_deny")
        if self.failure == "tool_transport":
            raise ConnectionError("Required MCP transport unavailable")
        if self.failure == "tool_result":
            return {"isError":True, "content":[{"type":"text", "text":"Target failed"}]}
        return {"content":[{"type":"text", "text":"hello"}]}


@unittest.skipUnless(HAS_LANGCHAIN, "LangChain is an optional example dependency")
class LangChainExampleTests(unittest.IsolatedAsyncioTestCase):
    async def agent(self, client):
        model = ExampleChatModel(client=client, provider="example", model_name="example")
        agent = create_agent(model, tools=[make_echo_tool(client, "target-1", "echo")])
        return await agent.ainvoke({"messages":[{"role":"user", "content":"Echo hello"}]},
                                  config={"recursion_limit":8})

    async def test_real_framework_loop_uses_model_tool_and_model_again(self):
        client = RecordingClient()
        result = await self.agent(client)
        self.assertEqual(result["messages"][-1].content, "Completed")
        self.assertEqual(len(client.models), 2)
        self.assertNotIn("memory_thread_id", client.models[0])
        self.assertEqual(client.tools[0][:3], ("target-1", "echo", {"text":"hello"}))
        self.assertEqual(client.tools[0][3]["tool_call_id"], "call-1")
        self.assertTrue(client.tools[0][3]["action_context"]["action_key"].startswith("langchain-"))
        self.assertNotEqual(client.tools[0][3]["action_context"]["action_key"], "call-1")
        self.assertEqual(client.models[0]["tool_definitions"][0]["function"]["name"], "governed_echo")
        self.assertNotIn("tool_call_id", client.models[0]["tool_definitions"][0]["function"]["parameters"]["properties"])
        self.assertEqual(client.models[1]["messages"][-1]["tool_call_id"], "call-1")

    async def test_tool_denial_ends_the_loop_without_retry_or_fallback(self):
        for failure, error in (("tool", PolicyViolationError), ("tool_transport", ConnectionError), ("tool_result", RuntimeError)):
            with self.subTest(failure=failure):
                client = RecordingClient(failure)
                with self.assertRaises(error):
                    await self.agent(client)
                self.assertEqual(len(client.models), 1)
                self.assertEqual(len(client.tools), 1)

    async def test_model_transport_failure_never_calls_a_tool(self):
        for failure, error in (("model", ConnectionError), ("model_deny", PolicyViolationError)):
            with self.subTest(failure=failure):
                client = RecordingClient(failure)
                with self.assertRaises(error):
                    await self.agent(client)
                self.assertEqual(len(client.models), 1)
                self.assertEqual(client.tools, [])

    async def test_message_translation_preserves_tool_arguments_and_correlation(self):
        messages = request_messages([
            HumanMessage(content="hello"),
            AIMessage(content="", tool_calls=[{"id":"id-1", "name":"echo", "args":{"text":"hello"}}]),
            ToolMessage(content="hello", tool_call_id="id-1"),
        ])
        self.assertEqual(messages[1]["tool_calls"][0]["function"]["arguments"], '{"text": "hello"}')
        self.assertEqual(messages[2]["tool_call_id"], "id-1")
        with self.assertRaises(ValueError):
            response_message({"output_text":"", "tool_requests":[{"id":"id-1", "name":"echo", "arguments":"bad"}]})
