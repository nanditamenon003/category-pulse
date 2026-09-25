"""
Category Pulse's AI agent: a Claude-powered retail floor assistant.

This is the centerpiece of the project. It uses Claude's tool use (function
calling) to ground every answer in real numbers pulled from the simulated
data — the agent never estimates or invents a figure. When a category is
behind pace, it is expected to investigate (stock, footfall, conversion)
before answering, the same way a good analyst would.
"""

import json
import os

from dotenv import load_dotenv

import kpi
import loyalty
import stock
from config import CATEGORIES, CLAUDE_MAX_TOKENS, CLAUDE_MODEL, DEFAULT_CURRENT_HOUR, MAX_TOOL_ITERATIONS

load_dotenv()


class AgentError(Exception):
    """Raised for problems the app should show as a friendly message, not a traceback."""


SYSTEM_PROMPT = """You are the Category Pulse floor assistant: an internal tool for store \
managers and sales associates in a single clothing store. You are not a shopping assistant \
and you never talk to customers directly.

Ground rules:
- Always call a tool to get real numbers before answering a question about sales, stock, \
traffic, or performance. Never estimate, round from memory, or invent a figure. If a tool \
call fails or a tool says data isn't available yet, say so plainly instead of guessing.
- When a category is behind pace, investigate before you answer: check its stock status and \
footfall (and conversion metrics, once available) to find the likely cause, rather than just \
reporting the pace number.
- Distinguish a traffic problem (footfall itself is low) from a conversion problem (footfall \
is normal but sales still collapsed — usually a stock, sizing, pricing, or service issue).
- Always end a diagnosis with one concrete, specific recommended action a manager or \
associate could actually do on the floor today.
- Be concise. A manager is reading this between customers, not studying a report. Prefer a \
short paragraph over a wall of text, and plain language over analyst jargon.
- Loyalty/cross-sell recommendations are segment-level only (by tier: Silver, Gold, \
Platinum, Non-member). Never reason about or reference individual customers — that is a \
deliberate privacy boundary for this tool, not a missing feature.
"""

TOOLS = [
    {
        "name": "get_category_pace",
        "description": (
            "Get pace/status (units sold so far vs. the expected pace for this point in "
            "the day) for one category or all categories. Status is 'behind', 'on_pace', "
            "'ahead', or 'too_early' (too few units expected so far for the percentage "
            "to mean anything — don't call a 'too_early' category behind or ahead). "
            "Use this first to see which categories need attention."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": CATEGORIES,
                    "description": "Limit to one category. Omit to get all categories.",
                },
                "hour": {
                    "type": "integer",
                    "description": (
                        "Store hour in 24h time (10-19) to evaluate pace as of. Omit to "
                        "use the current simulated hour."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_stock_status",
        "description": (
            "Get remaining stock by size for a category, as of right now, including which "
            "sizes (if any) are completely out. Use this to check whether a stockout is "
            "behind a category falling behind."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_footfall",
        "description": (
            "Get total visitor counts (foot traffic) for a category so far today. Compare "
            "against sales/pace to tell a traffic problem (low footfall) apart from a "
            "conversion problem (normal footfall, but sales still collapsed)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
                "hour": {
                    "type": "integer",
                    "description": "Store hour (10-19) to total visitors through. Omit for the current simulated hour.",
                },
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_conversion_metrics",
        "description": (
            "Get conversion rate (% of visitors who bought) and units-per-transaction (UPT) "
            "for a category. Not yet available in this build (Phase 6d)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
                "hour": {"type": "integer"},
            },
            "required": ["category"],
        },
    },
    {
        "name": "check_size_runs",
        "description": (
            "Check whether a category has a 'broken size run': core sizes (M, L) depleted "
            "even though total remaining stock still looks adequate. This is a distinct "
            "cause from a plain stockout. Not yet available in this build (Phase 6e)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_tier_playbook",
        "description": (
            "Get loyalty-tier (Silver/Gold/Platinum/Non-member) cross-sell response "
            "profiles for a category, to target a cross-sell recommendation at the tier "
            "most likely to respond. Not yet available in this build (Phase 6f)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
            },
            "required": ["category"],
        },
    },
]


def _build_tool_dispatch(current_hour):
    """
    Builds the name -> function map used to execute Claude's tool calls.
    Tools whose public signature (per the spec) doesn't take an hour default
    to `current_hour`, the "right now" of the simulated day, but still
    accept an explicit hour from Claude when the tool schema offers one.
    """

    def impl_get_category_pace(category=None, hour=None):
        h = hour if hour is not None else current_hour
        return kpi.get_category_pace(hour=h, category=category)

    def impl_get_stock_status(category):
        return stock.get_stock_status(category, hour=current_hour)

    def impl_get_footfall(category, hour=None):
        h = hour if hour is not None else current_hour
        return kpi.get_footfall(category=category, hour=h)

    def impl_get_conversion_metrics(category, hour=None):
        h = hour if hour is not None else current_hour
        return kpi.get_conversion_metrics(category=category, hour=h)

    def impl_check_size_runs(category):
        return stock.check_size_runs(category, hour=current_hour)

    def impl_get_tier_playbook(category):
        return loyalty.get_tier_playbook(category)

    return {
        "get_category_pace": impl_get_category_pace,
        "get_stock_status": impl_get_stock_status,
        "get_footfall": impl_get_footfall,
        "get_conversion_metrics": impl_get_conversion_metrics,
        "check_size_runs": impl_check_size_runs,
        "get_tier_playbook": impl_get_tier_playbook,
    }


def get_client():
    """
    Builds the Anthropic client, reading the API key from the environment
    (never hardcoded). Raises AgentError with a friendly message if the key
    is missing, so callers can show that instead of crashing.
    """
    # Imported lazily so a missing `anthropic` package still lets the rest
    # of the app (data generation, KPI engine) run without it installed.
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentError(
            "No ANTHROPIC_API_KEY found. Copy .env.example to .env and add your "
            "Anthropic API key, then try again."
        )
    return Anthropic(api_key=api_key)


def ask(question, current_hour=DEFAULT_CURRENT_HOUR, client=None):
    """
    Sends a question to Claude with tool use enabled, runs the tool-call
    loop until Claude has a final answer, and returns (answer_text,
    tool_call_log). tool_call_log is a list of {"tool", "input", "result"}
    dicts, in call order — used for the "How I got this" trace in Phase 7.

    Never raises for ordinary failure modes (missing key, API error): those
    come back as a friendly string answer with an empty tool_call_log, per
    the spec's "never crash with a raw traceback" requirement.
    """
    from anthropic import APIError

    tool_call_log = []

    try:
        if client is None:
            client = get_client()
    except AgentError as e:
        return str(e), tool_call_log

    dispatch = _build_tool_dispatch(current_hour)
    messages = [{"role": "user", "content": question}]

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        iterations = 0
        while response.stop_reason == "tool_use" and iterations < MAX_TOOL_ITERATIONS:
            iterations += 1
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_use_blocks:
                try:
                    result = dispatch[block.name](**block.input)
                except Exception as e:  # a bad tool call shouldn't crash the app
                    result = {"error": f"Tool call failed: {e}"}

                tool_call_log.append(
                    {"tool": block.name, "input": block.input, "result": result}
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    }
                )

            messages.append({"role": "user", "content": tool_results})
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        answer = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not answer:
            answer = "I wasn't able to reach a final answer for that — try rephrasing the question."
        return answer, tool_call_log

    except APIError as e:
        return _friendly_api_error(e), tool_call_log
    except Exception as e:
        return f"Something went wrong talking to Claude: {e}", tool_call_log


def _friendly_api_error(error):
    """Turns common API failures into a plain instruction instead of a raw error dump."""
    import anthropic

    if isinstance(error, anthropic.AuthenticationError):
        return ("The API key in the .env file isn't being accepted. Check it was copied in full "
                "from console.anthropic.com, then restart the app.")
    if isinstance(error, anthropic.BadRequestError) and "credit balance" in str(error).lower():
        return ("The AI chat needs API credits. Add a few dollars at console.anthropic.com "
                "(Plans & Billing), then ask again. Everything else on this page works without it.")
    if isinstance(error, anthropic.RateLimitError):
        return "Too many questions in a short time. Wait a minute and try again."
    if isinstance(error, anthropic.APIConnectionError):
        return "Couldn't reach Claude. Check the internet connection and try again."
    return f"The Claude API returned an error, so no answer this time. Details: {error}"


def _terminal_loop():
    print("Category Pulse agent (Phase 4 terminal interface)")
    print(f"Simulated 'current' store hour: {DEFAULT_CURRENT_HOUR}:00")
    print("Type a question, or 'quit' to exit.\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question.lower() in {"quit", "exit"}:
            break

        answer, tool_calls = ask(question)

        if tool_calls:
            print("\n[tools called]")
            for call in tool_calls:
                print(f"  - {call['tool']}({call['input']}) -> {call['result']}")
            print()

        print(f"Agent: {answer}\n")


if __name__ == "__main__":
    _terminal_loop()
