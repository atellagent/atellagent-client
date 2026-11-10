# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Text/tool-call adapter for this example, not a general LangChain provider."""

import json
from typing import Any
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

def request_messages(messages: list[BaseMessage]) -> list[dict]:
    roles = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    result = []
    for message in messages:
        if message.type not in roles or not isinstance(message.content, str):
            raise ValueError("This example supports text, system and tool messages only")
        item = {"role": roles[message.type], "content": message.content}
        if message.type == "tool":
            item["tool_call_id"] = message.tool_call_id
        if isinstance(message, AIMessage) and message.tool_calls:
            item["tool_calls"] = [{
                "id": call["id"], "type": "function", "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["args"], allow_nan=False),
                },
            } for call in message.tool_calls]
        result.append(item)
    return result


def response_message(result: dict) -> AIMessage:
    if not isinstance(result.get("output_text"), str) or not isinstance(result.get("tool_requests"), list):
        raise ValueError("The routed model did not return the documented result")
    calls = []
    for call in result["tool_requests"]:
        if (not isinstance(call, dict) or not isinstance(call.get("arguments"), dict)
                or not isinstance(call.get("id"), str) or not call["id"]
                or not isinstance(call.get("name"), str) or not call["name"]):
            raise ValueError("The routed model returned an invalid tool request")
        calls.append({"id": call["id"], "name": call["name"], "args": call["arguments"]})
    return AIMessage(content=result["output_text"], tool_calls=calls,
                     response_metadata={key: result[key] for key in ("response_id", "model", "provider") if key in result})


class ExampleChatModel(BaseChatModel):
    client: Any = Field(exclude=True, repr=False)
    model_name: str
    provider: str
    max_output_tokens: int = 512
    context_mode: str = 'caller'
    reasoning_effort: str | None = None

    @property
    def _llm_type(self) -> str:
        return "atellagent-example"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        if kwargs:
            raise ValueError("This example does not support additional tool-binding options")
        options = {"tool_definitions": [convert_to_openai_tool(tool) for tool in tools]}
        if tool_choice is not None:
            options["tool_choice"] = tool_choice
        return self.bind(**options)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("Use ainvoke; this example has no synchronous or direct-provider fallback")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if set(kwargs) - {"tool_definitions", "tool_choice"}:
            raise ValueError("Unsupported model options for this example")
        result = await self.client.invoke_model_async(
            messages=request_messages(messages), provider=self.provider, model=self.model_name,
            max_output_tokens=self.max_output_tokens, stream=False,
            stop_sequences=stop or [], parallel_tool_calls=False,
            poll_timeout_seconds=60, **kwargs,
            context_mode=self.context_mode,
            reasoning={"effort": self.reasoning_effort} if self.reasoning_effort is not None else None,
        )
        return ChatResult(generations=[ChatGeneration(message=response_message(result))])
