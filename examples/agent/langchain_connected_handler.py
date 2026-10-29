# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
"""Connected workflow handler using the same small LangChain model/tool adapter."""

import os
from langchain.agents import create_agent
from atellagent_client.connected import AgentParticipantActions
from atellagent_client.examples.agent.langchain_model import ExampleChatModel
from atellagent_client.examples.agent.langchain_agent_quickstart import make_echo_tool


async def handle(payload):
    actions = AgentParticipantActions()
    await actions.emit_agent_event_async(method='runtime_start', extra={
        'event_type': 'runtime_start', 'adapter': 'langchain', 'source': 'customer_sdk',
        'state_snapshot': {'phase': 'running'},
    })
    model = ExampleChatModel(client=actions, provider='custom',
        model_name=os.environ['ATELLAGENT_EXAMPLE_MODEL'],
        context_mode=os.environ.get('ATELLAGENT_EXAMPLE_CONTEXT_MODE', 'caller'),
        reasoning_effort=os.environ.get('ATELLAGENT_EXAMPLE_REASONING_EFFORT'))
    agent = create_agent(model, tools=[make_echo_tool(actions,
        os.environ['ATELLAGENT_EXAMPLE_MCP_TARGET'], 'echo')])
    result = await agent.ainvoke({'messages': payload['state']['messages']},
                                config={'recursion_limit': 8})
    return {'content': result['messages'][-1].content, 'metadata': {}}
