"""
TokenTracker — tracks API usage, calculates costs.
Pricing table adapted from CLAUDE_ADDIN/server/agent.py.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

# Стоимость за 1M токенов (input, output)
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001":   (0.80,   4.00),
    "claude-sonnet-4-5-20250929":  (3.00,  15.00),
    "claude-sonnet-4-6":           (3.00,  15.00),
    "claude-opus-4-6":             (15.00, 75.00),
}


def calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    rates = PRICING.get(model, (3.00, 15.00))  # default to Sonnet pricing
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000


class TokenTracker:
    """
    Accumulates token usage and costs across agents.
    Thread-safe. Appends to cost_log.jsonl for persistence.
    """

    def __init__(self, log_path: str | Path = "registry/cost_log.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "api_calls": 0,
        }

    def record(
        self,
        agent_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        task_id: str = "",
    ) -> float:
        """
        Record an API call. Returns cost in USD.
        Appends entry to cost_log.jsonl.
        """
        cost = calc_cost(model, input_tokens, output_tokens)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "task_id": task_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
        }

        with self._lock:
            self._totals["input_tokens"] += input_tokens
            self._totals["output_tokens"] += output_tokens
            self._totals["cost_usd"] += cost
            self._totals["api_calls"] += 1

            # Append to JSONL log
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        return cost

    def get_totals(self) -> dict:
        """Return accumulated totals."""
        with self._lock:
            return {**self._totals, "cost_usd": round(self._totals["cost_usd"], 6)}

    def get_agent_costs(self) -> dict[str, float]:
        """Read log and sum costs per agent."""
        costs: dict[str, float] = {}
        if not self.log_path.exists():
            return costs
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    aid = entry.get("agent_id", "unknown")
                    costs[aid] = costs.get(aid, 0.0) + entry.get("cost_usd", 0.0)
        return {k: round(v, 6) for k, v in costs.items()}
