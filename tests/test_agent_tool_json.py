"""Exercise tool-call JSON handling through both research loops."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.base_agent import BaseDeepResearchAgent
from tool_client.schemas import ToolResponse


class ScriptedAgent(BaseDeepResearchAgent):
    def __init__(self, responses, config_path):
        super().__init__(tool_config_path=str(config_path))
        self.responses = iter(responses)
        self.prompts = []

    async def generate(self, messages, temperature=0.7, top_p=0.9):
        self.prompts.append(list(messages))
        return next(self.responses)


def make_agent(tmp_path, tool_calls):
    responses = [f"<tool_call>{call}</tool_call>" for call in tool_calls]
    responses += ["<answer>Found it.</answer>", "<verification_result>CORRECT</verification_result>"]
    agent = ScriptedAgent(responses, tmp_path / "no-tools.yaml")
    tool = SimpleNamespace(
        create=AsyncMock(return_value=("search-instance", None)),
        execute=AsyncMock(return_value=(ToolResponse(text="Search evidence"), 0, {})),
        release=AsyncMock(),
    )
    agent.tools = {"web_search": tool}
    return agent, tool


async def run_agent(agent, streaming):
    if streaming:
        updates = [update async for update in agent.run_stream("Find the meaning.")]
        assert not [update for update in updates if update["type"] == "error"]
        assert updates[-1] == {"type": "done", "think": None, "answer": "Found it."}
        return updates
    assert await agent.run("Find the meaning.") == "Found it."
    return []


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_validated_tool_json_reaches_execution(tmp_path, streaming, repair):
    query = r"meaning of \Delta in physics"
    call = json.dumps({"name": "web_search", "arguments": {"query_list": [query]}})
    if repair:
        # Small models may emit a LaTeX backslash without the JSON escape.
        call = call.replace("\\\\", "\\")
    agent, tool = make_agent(tmp_path, [call])

    updates = asyncio.run(run_agent(agent, streaming))

    tool.execute.assert_awaited_once_with("search-instance", {"query_list": [query]})
    assert {"role": "tool", "content": "Search evidence"} in agent.prompts[1]
    if streaming:
        tool_updates = [update for update in updates if update["type"] == "tool_call"]
        assert tool_updates[0]["queries"] == [query]
    tool.release.assert_awaited_once_with("search-instance")
    assert agent.tool_instances == {}


@pytest.mark.parametrize("streaming", [False, True])
def test_unrepairable_json_returns_feedback_and_allows_a_retry(tmp_path, streaming):
    valid_call = json.dumps({"name": "web_search", "arguments": {"query_list": ["retry"]}})
    agent, tool = make_agent(tmp_path, ['{"name":', valid_call])

    asyncio.run(run_agent(agent, streaming))

    tool.execute.assert_awaited_once_with("search-instance", {"query_list": ["retry"]})
    feedback = agent.prompts[1][-1]
    assert feedback["role"] == "tool"
    assert "Invalid JSON" in feedback["content"]
    tool.release.assert_awaited_once_with("search-instance")
