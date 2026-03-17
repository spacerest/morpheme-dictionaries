"""
Shared cost tracking utility for Claude API calls.

Usage in scripts:
    from cost_tracker import CostTracker
    tracker = CostTracker(script="regloss_cross_pairs", pair="de-ru", model=model)
    # after each API call:
    tracker.add(response.usage)
    # at end of script:
    tracker.finish()
"""

from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "api_costs.md"

# USD per million tokens (input, output)
_PRICES = {
    "claude-haiku-4-5-20251001":  (0.80,  4.00),
    "claude-haiku-4-5":           (0.80,  4.00),
    "claude-sonnet-4-6":          (3.00, 15.00),
    "claude-sonnet-4-5":          (3.00, 15.00),
    "claude-opus-4-6":           (15.00, 75.00),
    "claude-opus-4-5":           (15.00, 75.00),
}


def _price(model: str) -> tuple[float, float]:
    for key, prices in _PRICES.items():
        if model.startswith(key) or key.startswith(model):
            return prices
    # fallback: unknown model, return 0 so we still log tokens
    return (0.0, 0.0)


def _ensure_header():
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
        LOG_FILE.write_text(
            "# API Cost Log\n\n"
            "| Date (UTC) | Script | Pair | Model | Input Tokens | Output Tokens | Cost (USD) |\n"
            "|------------|--------|------|-------|-------------:|--------------:|-----------:|\n"
        )


class CostTracker:
    def __init__(self, script: str, pair: str, model: str):
        self.script = script
        self.pair = pair
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage):
        """Pass response.usage from an Anthropic API response."""
        self.input_tokens += getattr(usage, "input_tokens", 0)
        self.output_tokens += getattr(usage, "output_tokens", 0)

    def cost_usd(self) -> float:
        price_in, price_out = _price(self.model)
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000

    def summary(self) -> str:
        cost = self.cost_usd()
        return (
            f"Tokens: {self.input_tokens:,} in / {self.output_tokens:,} out"
            f" — est. ${cost:.4f}"
        )

    def finish(self):
        """Print summary and append a row to api_costs.md."""
        print(f"[{self.pair}] {self.summary()}")
        _ensure_header()
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        cost = self.cost_usd()
        model_short = self.model.replace("claude-", "").replace("-20251001", "")
        row = (
            f"| {date} | {self.script} | {self.pair} | {model_short} "
            f"| {self.input_tokens:,} | {self.output_tokens:,} | ${cost:.4f} |\n"
        )
        with LOG_FILE.open("a") as f:
            f.write(row)
