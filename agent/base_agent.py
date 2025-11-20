# Copyright 2025 Pokee AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Base Deep Research Agent

This module provides the abstract base class for deep research agents that can:
- Search the web for information
- Read and analyze web content
- Verify answers for correctness
- Iterate through research and verification modes until finding a correct answer

The agent operates in two modes:
1. Research mode: Gathers information using tools (web_search, web_read)
2. Verification mode: Verifies the answer and decides to accept or retry
"""

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, Tuple

from pydantic import BaseModel, field_validator

from agent.prompt_utils import construct_prompt
from logging_utils import setup_colored_logger
from tool_client.schemas import ToolResponse
from tool_client.tool_registry import initialize_tools_from_config

logger = setup_colored_logger(__name__)


class SearchArgs(BaseModel):
    """Arguments for web search tool calls.

    Attributes:
        query_list: List of search queries to execute
    """

    query_list: List[str]

    @field_validator("query_list")
    @classmethod
    def validate_query_list(cls, v):
        """Validate that query list contains at least one non-empty query.

        Args:
            v: Raw query list from tool call

        Returns:
            Cleaned list of non-empty queries

        Raises:
            ValueError: If no valid queries are provided
        """
        queries = [q.strip() for q in v if q and q.strip()]
        if len(queries) == 0:
            raise ValueError("query_list must contain at least one non-empty query")
        return queries


class ReadArgs(BaseModel):
    """Arguments for web read tool calls.

    Attributes:
        url_list: List of URLs to read and analyze
    """

    url_list: List[str]

    @field_validator("url_list")
    @classmethod
    def validate_url_list(cls, v):
        """Validate that URL list contains at least one non-empty URL.

        Args:
            v: Raw URL list from tool call

        Returns:
            Cleaned list of non-empty URLs

        Raises:
            ValueError: If no valid URLs are provided
        """
        urls = [u.strip() for u in v if u and u.strip()]
        if len(urls) == 0:
            raise ValueError("url_list must contain at least one non-empty URL")
        return urls


class BaseDeepResearchAgent(ABC):
    """Base class for deep research agents with shared functionality.

    This abstract class provides common functionality for research agents including:
    - Tool management and initialization
    - Response parsing with answer verification
    - Research loop execution with mode transitions
    - Tool instance lifecycle management
    - Streaming updates for real-time UI integration

    The agent operates in a two-mode loop:
    1. Research mode: Agent makes tool calls (web_search, web_read) to gather information
    2. Verification mode: Agent verifies its answer and decides to accept or retry

    Subclasses must implement the `generate()` method for model-specific inference.

    Example:
        ```python
        agent = ConcreteAgent(tool_config_path="config/tools.yaml")
        answer = await agent.run("What is quantum computing?")

        # Or use streaming for real-time updates
        async for update in agent.run_stream("What is quantum computing?"):
            if update["type"] == "tool_call":
                print(f"Searching: {update['queries']}")
            elif update["type"] == "done":
                print(f"Answer: {update['answer']}")
        ```
    """

    def __init__(
        self,
        tool_config_path: str = "config/tool_config/pokee_tool_config.yaml",
        max_turns: int = 10,
        max_tool_response_length: int = 32768,
    ):
        """Initialize the base agent with tools and configuration.

        Args:
            tool_config_path: Path to tool configuration YAML file
            max_turns: Maximum conversation turns before giving up
            max_tool_response_length: Maximum length for tool response text (truncated if exceeded)
        """
        # Initialize tools
        self.tools = {}
        self.tool_instances = {}

        if Path(tool_config_path).exists():
            tool_list = initialize_tools_from_config(tool_config_path)
            self.tools = {tool.name: tool for tool in tool_list}
            self.tool_schemas = [
                tool.tool_schema.model_dump(exclude_unset=True, exclude_none=True)
                for tool in tool_list
            ]
            logger.info(f"Initialized tools: {list(self.tools.keys())}")
        else:
            logger.warning(f"Tool config not found at {tool_config_path}")
            self.tool_schemas = []

        self.max_turns = max_turns
        self.max_tool_response_length = max_tool_response_length

        # Initialize regex patterns for parsing agent responses
        self._init_regex_patterns()

        # Instance counter for unique tool instance IDs
        self.instance_counter = 0

    def _init_regex_patterns(self):
        """Initialize regex patterns for parsing agent responses.

        Patterns extract content from XML-like tags in agent output:
        - <think>...</think>: Agent's reasoning process
        - <tool_call>...</tool_call>: Tool invocation with JSON args
        - <answer>...</answer>: Proposed answer to the question
        - <verification_result>...</verification_result>: CORRECT or INCORRECT
        """
        self.think_regex = re.compile(r"<think>(.*?)</think>", re.DOTALL)
        self.tool_call_regex = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
        self.answer_regex = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)
        self.verification_result_regex = re.compile(
            r"<verification_result>(.*?)</verification_result>", re.DOTALL
        )

    def _check_tool_call_format(
        self, tool_call_str: str
    ) -> Tuple[bool, Optional[ToolResponse]]:
        """Validate tool call format and structure.

        Checks that:
        - Tool call is valid JSON
        - Contains 'name' and 'arguments' fields
        - Tool name exists in available tools
        - Tool instance is initialized

        Args:
            tool_call_str: JSON string with tool call data

        Returns:
            Tuple of (is_valid, error_response)
            - is_valid: True if format is correct, False otherwise
            - error_response: ToolResponse with error message if invalid, None if valid
        """
        # Parse tool call JSON
        try:
            tool_call = json.loads(tool_call_str)
        except json.JSONDecodeError:
            # Try escaping backslashes for common JSON errors
            tool_call_escaped = tool_call_str.replace("\\", "\\\\")
            try:
                tool_call = json.loads(tool_call_escaped)
            except json.JSONDecodeError:
                return False, ToolResponse(
                    text=f"Error: Invalid JSON in tool call: {tool_call_str}"
                )

        # Validate tool call structure
        if (
            not isinstance(tool_call, dict)
            or "name" not in tool_call
            or "arguments" not in tool_call
        ):
            return False, ToolResponse(
                text="Error: Tool call must have 'name' and 'arguments' fields"
            )

        tool_name = tool_call["name"]

        # Validate tool exists
        if tool_name not in self.tools:
            return False, ToolResponse(
                text=f"Error: Unknown tool '{tool_name}'. Available tools: {list(self.tools.keys())}"
            )

        # Validate tool instance exists
        if tool_name not in self.tool_instances:
            return False, ToolResponse(
                text=f"Error: Tool instance not initialized for '{tool_name}'"
            )

        return True, None

    def parse_response(self, text: str, current_mode: str) -> Dict:
        """Parse agent response and determine next action.

        The agent operates in two modes:
        - Research mode: Agent makes tool calls to gather information
        - Verification mode: Agent verifies the answer for correctness

        Response patterns handled:
        1. Tool call only: Execute tool and continue in current mode
        2. Answer only: Switch to verification mode
        3. Answer + verification result: Extract answer and either finish (CORRECT)
           or return to research mode (INCORRECT)
        4. Verification result only: Finish (CORRECT) or return to research (INCORRECT)
        5. Missing expected tags: Generate error message for regeneration

        Args:
            text: Generated response text from the agent
            current_mode: Current operating mode ('research' or 'verification')

        Returns:
            Dictionary with keys:
                - tool_messages: Messages to append to conversation (mode transitions/errors)
                - tool_calls: Tool calls to execute (list with 0 or 1 element)
                - generation_finished: Whether generation is complete
                - current_mode: Updated mode ('research' or 'verification')
                - think: Agent's reasoning text (or None)
                - answer: Extracted answer text (or None)
        """
        output = {
            "tool_messages": [],
            "tool_calls": [],
            "generation_finished": False,
            "current_mode": current_mode,
            "think": None,
            "answer": None,
        }

        # Extract thinking
        think_matches = self.think_regex.findall(text)
        if len(think_matches) > 0:
            output["think"] = think_matches[0].strip()

        # Check for tool call
        tool_call_matches = self.tool_call_regex.findall(text)
        num_tool_calls = len(tool_call_matches)

        if num_tool_calls == 0:
            # No tool call found - generate error message based on mode
            if current_mode == "research":
                output["tool_messages"] = [
                    {
                        "role": "user",
                        "content": "You are in the research mode but didn't generate a <tool_call> block. Please try again.",
                    }
                ]
            else:  # verification mode
                output["tool_messages"] = [
                    {
                        "role": "user",
                        "content": "You are in the verification mode but didn't generate a <verification_result> block. Please try again.",
                    }
                ]
        elif num_tool_calls > 1:
            # Multiple tool calls not allowed
            output["tool_messages"] = [
                {
                    "role": "user",
                    "content": "You are only allowed to generate one tool call but you generated more than one. Please regenerate the tool call.",
                }
            ]
        else:
            # Exactly one tool call found
            output["tool_calls"] = [tool_call_matches[0].strip()]

        # Check for answer (overrides tool call behavior)
        answer_matches = self.answer_regex.findall(text)
        num_answers = len(answer_matches)

        if num_answers > 0:
            output["tool_calls"].clear()  # Clear tool calls if answer is present

            if num_answers == 1:
                output["tool_messages"] = [
                    {
                        "role": "user",
                        "content": "You have provided an answer. ##The verification mode starts##.",
                    }
                ]
                output["current_mode"] = "verification"
                output["answer"] = answer_matches[0].strip()
            else:
                # Multiple answers not allowed
                output["tool_messages"] = [
                    {
                        "role": "user",
                        "content": "You are only allowed to generate one answer but you generated more than one. Please regenerate the answer.",
                    }
                ]

        # Check for verification result (overrides everything else)
        verification_result_matches = self.verification_result_regex.findall(text)
        num_verification_results = len(verification_result_matches)

        if num_verification_results > 0:
            output[
                "tool_calls"
            ].clear()  # Clear tool calls if verification result is present

            if num_verification_results == 1:
                result = verification_result_matches[0].strip().lower()

                if result == "correct":
                    output["generation_finished"] = True
                    output["tool_messages"].clear()  # Clear messages when finished
                elif result == "incorrect":
                    output["tool_messages"] = [
                        {
                            "role": "user",
                            "content": "The answer is verified to be incorrect. Please incorporate the feedback from the verification mode and re-enter the research mode. ##The research mode starts##.",
                        }
                    ]
                    output["current_mode"] = "research"
                    output["answer"] = None
                else:
                    # Invalid verification result format
                    output["tool_messages"] = [
                        {
                            "role": "user",
                            "content": "Your verification result should be either CORRECT or INCORRECT, without any other text. Please redo the verification mode.",
                        }
                    ]
                    output["answer"] = None
            else:
                # Multiple verification results not allowed
                output["tool_messages"] = [
                    {
                        "role": "user",
                        "content": "You are only allowed to generate one verification result but you generated more than one. Please redo the verification mode and only generate one verification result.",
                    }
                ]
                output["answer"] = None

        return output

    @abstractmethod
    async def generate(
        self, messages: list[dict], temperature: float = 0.7, top_p: float = 0.9
    ) -> str:
        """Generate response from messages. Must be implemented by subclasses.

        This method handles the actual model inference. Subclasses should implement
        this based on their specific model type (local, VLLM, API, etc.).

        Args:
            messages: Conversation messages in chat format (OpenAI-style)
                     Each message is a dict with 'role' and 'content' keys
            temperature: Sampling temperature (0.0 = deterministic, higher = more random)
            top_p: Nucleus sampling parameter (0.0-1.0)

        Returns:
            Generated text response from the model
        """
        pass

    async def initialize_tool_instances(self, question: str) -> None:
        """Initialize tool instances for all available tools.

        Creates a unique instance for each tool to maintain isolated state
        for this research session. Each instance gets a unique ID and the
        research question as context.

        Args:
            question: The research question (passed to tool instances for context)
        """
        self.tool_instances = {}
        
        # Deduplication tracking - prevents redundant tool calls within a research session
        self._seen_queries = set()  # Track searched queries to prevent duplicate searches
        self._seen_urls = set()     # Track read URLs to prevent duplicate reads
        
        for tool_name, tool in self.tools.items():
            instance_id, _ = await tool.create(
                create_kwargs={
                    "idx": self.instance_counter,
                    "question": question,
                }
            )
            self.tool_instances[tool_name] = instance_id
            self.instance_counter += 1

    async def cleanup_tool_instances(self) -> None:
        """Release all tool instances and clean up resources.

        Should be called after research is complete or if an error occurs.
        Ensures proper cleanup of tool state and resources.
        """
        for tool_name, instance_id in self.tool_instances.items():
            if tool_name in self.tools:
                await self.tools[tool_name].release(instance_id)
        self.tool_instances = {}

    async def call_tool(self, tool_call_str: str) -> ToolResponse:
        """Execute a tool call and return the response.

        Parses the tool call JSON, executes the tool with the given arguments,
        and returns the result. Automatically truncates long responses.

        Args:
            tool_call_str: JSON string containing tool name and arguments
                          Format: {"name": "tool_name", "arguments": {...}}

        Returns:
            ToolResponse with text result and optional artifacts
        """
        tool_call = json.loads(tool_call_str)
        tool_name = tool_call["name"]
        tool_args = tool_call["arguments"]

        # Deduplication for web_search - prevents redundant searches
        if tool_name == "web_search" and "query_list" in tool_args:
            original_queries = tool_args["query_list"]
            tool_args["query_list"] = [q for q in original_queries if q not in self._seen_queries]
            
            # If all queries are duplicates, return cached response without calling tool
            if not tool_args["query_list"]:
                logger.info(f"All {len(original_queries)} queries already seen, skipping search")
                response_text = json.dumps([
                    {"query": q, "search_results": [], "note": "Already searched"} 
                    for q in original_queries
                ])
                # Truncate cached response if needed for consistency
                if len(response_text) > self.max_tool_response_length:
                    response_text = response_text[:self.max_tool_response_length] + "...(truncated)"
                return ToolResponse(text=response_text)
            
            # Mark queries as seen before execution
            self._seen_queries.update(tool_args["query_list"])
        
        # Deduplication for web_read - prevents redundant URL reads
        elif tool_name == "web_read" and "url_list" in tool_args:
            original_urls = tool_args["url_list"]
            tool_args["url_list"] = [u for u in original_urls if u not in self._seen_urls]
            
            # If all URLs are duplicates, return cached response without calling tool
            if not tool_args["url_list"]:
                logger.info(f"All {len(original_urls)} URLs already seen, skipping read")
                response_text = json.dumps([
                    {"url": u, "content": "", "note": "Already read"} 
                    for u in original_urls
                ])
                # Truncate cached response if needed for consistency
                if len(response_text) > self.max_tool_response_length:
                    response_text = response_text[:self.max_tool_response_length] + "...(truncated)"
                return ToolResponse(text=response_text)
            
            # Mark URLs as seen before execution
            self._seen_urls.update(tool_args["url_list"])

        # Execute tool
        tool = self.tools[tool_name]
        instance_id = self.tool_instances[tool_name]

        try:
            tool_response, _, _ = await tool.execute(instance_id, tool_args)

            # Truncate response if too long
            if (
                tool_response.text
                and len(tool_response.text) > self.max_tool_response_length
            ):
                tool_response.text = (
                    tool_response.text[: self.max_tool_response_length]
                    + "...(truncated)"
                )

            return tool_response
        except Exception as e:
            return ToolResponse(
                text=f"Error executing tool: {type(e).__name__}: {str(e)}"
            )

    async def run(
        self,
        question_raw: str,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_turns: Optional[int] = None,
        verbose: bool = False,
    ) -> str:
        """Run the research agent to answer a question (blocking mode).

        The agent operates in a loop, alternating between research and verification modes:
        1. Research mode: Makes tool calls to gather information (web_search, web_read)
        2. Verification mode: Verifies the answer for correctness
        3. If verification fails, returns to research mode with feedback
        4. If verification passes, returns the final answer

        Args:
            question_raw: Research question from user
            temperature: Sampling temperature for generation (0.0-2.0, default 0.7)
            top_p: Nucleus sampling parameter for generation (0.0-1.0, default 0.9)
            max_turns: Maximum number of turns (overrides instance default if provided)
            verbose: Whether to print detailed progress information to console

        Returns:
            Final answer string, or error message if max turns reached without answer

        Example:
            ```python
            agent = ConcreteAgent()
            answer = await agent.run(
                "What is the capital of France?",
                temperature=0.7,
                verbose=True
            )
            print(answer)  # "Paris is the capital of France..."
            ```
        """
        await self.initialize_tool_instances(question_raw)
        _max_turns = max_turns if max_turns is not None else self.max_turns

        try:
            # Construct initial prompt
            messages = construct_prompt(question_raw)
            current_mode = "research"
            turn_count = 0
            tentative_answer = None

            # Main research loop
            while turn_count < _max_turns:
                if verbose:
                    print(f"\n{'=' * 60}")
                    print(f"Turn {turn_count + 1} - Mode: {current_mode}")
                    print(f"{'=' * 60}")

                # Generate response
                response = await self.generate(messages, temperature, top_p)

                if verbose:
                    print(f"\nAgent: {response}\n")

                messages.append({"role": "assistant", "content": response})
                turn_count += 1

                # Parse response to determine next action
                parse_result = self.parse_response(response, current_mode)

                tool_messages = parse_result["tool_messages"]
                tool_calls = parse_result["tool_calls"]
                generation_finished = parse_result["generation_finished"]
                current_mode = parse_result["current_mode"]

                # Update tentative answer if found
                if (
                    parse_result["answer"] is not None
                    and parse_result["answer"].strip()
                ):
                    tentative_answer = parse_result["answer"].strip()

                # Check if generation is finished
                if generation_finished:
                    return (
                        tentative_answer
                        if tentative_answer
                        else "Generation finished but answer not found"
                    )

                # Handle mode transitions or error corrections
                if tool_messages:
                    messages.extend(tool_messages)
                    continue

                # Execute tool calls
                if tool_calls:
                    tool_call_str = tool_calls[0]  # Only one tool call supported

                    if verbose:
                        print(f"\nCalling tool: {tool_call_str[:200]}...")

                    is_valid, error_response = self._check_tool_call_format(
                        tool_call_str
                    )
                    if is_valid:
                        tool_response = await self.call_tool(tool_call_str)
                    else:
                        tool_response = error_response

                    if verbose:
                        print(f"Tool response: {tool_response.text[:200]}...")

                    messages.append(
                        {"role": "tool", "content": tool_response.text or ""}
                    )
                    continue

            return "Maximum turns reached without finding answer"

        finally:
            # Always cleanup tool instances
            await self.cleanup_tool_instances()

    async def run_stream(
        self,
        question_raw: str,
        temperature: float = 0.1,
        top_p: float = 0.1,
        max_turns: Optional[int] = None,
    ) -> AsyncIterator[Dict[str, any]]:
        """Run the research agent with step-by-step streaming updates.

        Similar to `run()` but yields dictionaries with updates after each step,
        enabling real-time UI updates and progress tracking.

        Update types yielded:
        - tool_call: Agent is calling a tool (web_search or web_read)
          - web_search: {"type": "tool_call", "tool_name": "web_search", "think": "...", "queries": [...]}
          - web_read: {"type": "tool_call", "tool_name": "web_read", "think": "...", "urls": [...]}
        - answer_found: Agent found an answer and switched to verification mode
          - {"type": "answer_found", "think": "..."}
        - done: Research completed successfully
          - {"type": "done", "think": "...", "answer": "..."}
        - error: An error occurred during research
          - {"type": "error", "message": "..."}

        Args:
            question_raw: Research question from user
            temperature: Sampling temperature for generation (default 0.1 for focused results)
            top_p: Nucleus sampling parameter (default 0.1 for focused results)
            max_turns: Maximum number of turns (overrides instance default if provided)

        Yields:
            Dictionary with update information for each step

        Example:
            ```python
            agent = ConcreteAgent()
            async for update in agent.run_stream("What is quantum computing?"):
                if update["type"] == "tool_call":
                    print(f"Searching: {update.get('queries', update.get('urls'))}")
                elif update["type"] == "answer_found":
                    print("Found answer, verifying...")
                elif update["type"] == "done":
                    print(f"Final answer: {update['answer']}")
            ```
        """
        await self.initialize_tool_instances(question_raw)
        _max_turns = max_turns if max_turns is not None else self.max_turns

        try:
            messages = construct_prompt(question_raw)
            current_mode = "research"
            turn_count = 0
            tentative_answer = None

            while turn_count < _max_turns:
                # Generate response
                response = await self.generate(messages, temperature, top_p)
                messages.append({"role": "assistant", "content": response})
                turn_count += 1

                # Parse response
                parse_result = self.parse_response(response, current_mode)

                think = parse_result["think"]
                tool_messages = parse_result["tool_messages"]
                tool_calls = parse_result["tool_calls"]
                generation_finished = parse_result["generation_finished"]
                current_mode = parse_result["current_mode"]

                # Update tentative answer if found
                if (
                    parse_result["answer"] is not None
                    and parse_result["answer"].strip()
                ):
                    tentative_answer = parse_result["answer"].strip()

                    # Yield answer found update
                    yield {"type": "answer_found", "think": think}

                # Check if generation is finished
                if generation_finished:
                    yield {
                        "type": "done",
                        "think": think,
                        "answer": tentative_answer
                        or "Generation finished but answer not found",
                    }
                    return

                # Handle mode transitions or error corrections
                if tool_messages:
                    messages.extend(tool_messages)
                    continue

                # Execute tool calls
                if tool_calls:
                    tool_call_str = tool_calls[0]

                    is_valid, error_response = self._check_tool_call_format(
                        tool_call_str
                    )
                    if is_valid:
                        tool_call = json.loads(tool_call_str)
                        tool_name = tool_call["name"]
                        tool_args = tool_call["arguments"]

                        # Yield tool call update with details
                        if tool_name == "web_search":
                            try:
                                search_args = SearchArgs.model_validate(tool_args)
                                yield {
                                    "type": "tool_call",
                                    "tool_name": "web_search",
                                    "think": think,
                                    "queries": search_args.query_list,
                                }
                            except Exception:
                                # Don't expose validation errors to user
                                pass
                        elif tool_name == "web_read":
                            try:
                                read_args = ReadArgs.model_validate(tool_args)
                                yield {
                                    "type": "tool_call",
                                    "tool_name": "web_read",
                                    "think": think,
                                    "urls": read_args.url_list,
                                }
                            except Exception:
                                # Don't expose validation errors to user
                                pass

                        # Execute tool
                        tool_response = await self.call_tool(tool_call_str)
                    else:
                        tool_response = error_response

                    messages.append(
                        {"role": "tool", "content": tool_response.text or ""}
                    )

            yield {
                "type": "done",
                "answer": "Maximum turns reached without finding answer. Set a higher turn limit and try again.",
            }

        except Exception as e:
            yield {
                "type": "error",
                "message": str(e),
            }
        finally:
            await self.cleanup_tool_instances()
