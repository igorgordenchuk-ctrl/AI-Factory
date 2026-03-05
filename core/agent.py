"""
BaseAgent — core agent with tool-use loop.
Adapted from CLAUDE_ADDIN/server/agent.py, using official anthropic SDK.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic

from core.token_tracker import TokenTracker, calc_cost

logger = logging.getLogger(__name__)

MAX_TOKENS = 4096
MAX_TURNS = 10


@dataclass
class AgentResult:
    """Result of a single agent run."""
    response: str = ""
    cost: float = 0.0
    tool_calls: list[str] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    turns: int = 0
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    error: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)


class BaseAgent:
    """
    Core agent with tool-use loop.

    Subclasses (Foreman, Worker, Supervisor) customize:
    - system_prompt: role-specific instructions
    - tools_definition(): list of tool schemas
    - execute_tool(): tool routing
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        model: str = "claude-sonnet-4-6",
        system_prompt: str = "",
        max_turns: int = MAX_TURNS,
        max_tokens: int = MAX_TOKENS,
        token_tracker: Optional[TokenTracker] = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.token_tracker = token_tracker
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY env var
        return self._client

    def tools_definition(self) -> list[dict]:
        """Return list of tool definitions for Claude API. Override in subclasses."""
        return []

    def execute_tool(self, name: str, inputs: dict) -> str:
        """Execute a tool call. Override in subclasses."""
        return f"Tool '{name}' not implemented"

    def run(self, message: str, context: Optional[dict] = None, task_id: str = "") -> AgentResult:
        """
        Main tool-use loop — adapted from CLAUDE_ADDIN agent.py lines 93-183.

        1. Build messages
        2. Call Claude API with tools
        3. If tool_use blocks: execute tools, send results back, continue
        4. If no tools: done
        5. Return AgentResult
        """
        tools_def = self.tools_definition()
        messages: list[dict] = []

        # Добавляем контекст если есть
        if context:
            ctx_text = f"Context:\n```json\n{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n\n"
            messages.append({"role": "user", "content": ctx_text + message})
        else:
            messages.append({"role": "user", "content": message})

        text_out = ""
        total_in = 0
        total_out = 0
        tool_calls_made: list[str] = []
        tool_log: list[dict] = []
        error = None

        # ── Tool Use Loop ──
        for turn in range(self.max_turns):
            try:
                # Формируем параметры вызова
                api_params: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "system": self.system_prompt,
                    "messages": messages,
                }
                if tools_def:
                    api_params["tools"] = tools_def

                response = self.client.messages.create(**api_params)

            except anthropic.APIError as e:
                error = str(e)
                logger.error(f"[{self.agent_id}] API ошибка: {error}")
                break

            # Считаем токены
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens

            if self.token_tracker:
                self.token_tracker.record(
                    agent_id=self.agent_id,
                    model=self.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    task_id=task_id,
                )

            # Добавляем ответ ассистента в историю
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            messages.append({"role": "assistant", "content": assistant_content})

            # Обрабатываем блоки ответа
            used_tool = False
            tool_results = []

            for block in response.content:
                if block.type == "text":
                    text_out += block.text + "\n"

                elif block.type == "tool_use":
                    used_tool = True
                    tool_name = block.name
                    tool_input = block.input
                    tool_id = block.id
                    tool_calls_made.append(tool_name)

                    # Выполняем инструмент и замеряем время
                    t0 = time.time()
                    try:
                        tool_result = self.execute_tool(tool_name, tool_input)
                    except Exception as e:
                        tool_result = f"Error: {e}"
                        logger.error(f"[{self.agent_id}] Ошибка инструмента {tool_name}: {e}")
                    elapsed_ms = int((time.time() - t0) * 1000)

                    # Лог
                    tool_log.append({
                        "turn": turn + 1,
                        "tool": tool_name,
                        "input": str(tool_input)[:200],
                        "result": str(tool_result)[:200],
                        "ms": elapsed_ms,
                    })

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": str(tool_result),
                    })

            # Если были вызовы инструментов — продолжаем цикл
            if used_tool and tool_results:
                messages.append({"role": "user", "content": tool_results})
                continue

            # Нет инструментов — Claude закончил
            break

        # Обработка max_tokens
        if response.stop_reason == "max_tokens":
            text_out += "\n\n[Ответ обрезан — достигнут лимит токенов]"

        cost = calc_cost(self.model, total_in, total_out)

        return AgentResult(
            response=text_out.strip(),
            cost=round(cost, 6),
            tool_calls=tool_calls_made,
            tool_log=tool_log,
            turns=turn + 1,
            tokens={"input": total_in, "output": total_out},
            error=error,
        )
