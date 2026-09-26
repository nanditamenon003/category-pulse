"""
Category Pulse's AI agent: a retail floor assistant that answers questions by
calling tools (function calling) that read the store's own numbers.

It never estimates or invents a figure: every number in an answer comes from
a tool result, and the "How I got this" trace in the app shows which. When a
category is behind, it investigates (stock, deliveries, visitors, buying)
before answering, the way a good analyst would.

The provider is one setting in config.py (AI_PROVIDER). The code uses the
Anthropic Messages API; DeepSeek's Anthropic-compatible endpoint runs the
same code unchanged.

The chat runs on the demo store only: a store's own uploaded data is never
sent to an AI provider. Its tools still read through a Store, like the rest
of the app.
"""

import json
import os

from dotenv import load_dotenv

import kpi
import loyalty
import staffing
import stock
from config import (
    AI_MAX_TOKENS,
    AI_PROVIDER,
    AI_PROVIDERS,
    CATEGORIES,
    DEPARTMENTS,
    LINES,
    MAX_TOOL_ITERATIONS,
)
from store import resolve

load_dotenv()

PROVIDER = AI_PROVIDERS[AI_PROVIDER]


class AgentError(Exception):
    """Raised for problems the app should show as a friendly message, not a traceback."""


def build_system_prompt(current_hour, store):
    now = "20:00, closing time" if current_hour == 19 else f"{current_hour + 1}:00"
    return f"""You are Category Pulse, an internal assistant for the manager and sales associates \
of a single clothing store. You help the floor team act on category performance. You never talk \
to customers.

Where things stand: it is day {store.today_day} of a {store.days_in_month}-day month, and the time now is \
{now}. Tools only return data up to now. In tool results, "hour": 16 means the 16:00-17:00 \
selling hour, so data "as of hour 16" runs to 17:00; when you mention the time, say {now}.

How the store is organised:
- Departments (floor zones): Menswear, Womenswear, Kidswear.
- Lines: Men Casual, Men Formal, Men Denim, Women, and kids lines Boys and Girls (8-16 years) \
and Little Boys and Little Girls (2-7 years).
- A category is "<line> <product type>", e.g. "Women Tops". Everyday names: "chinos" means \
Men Casual Trousers; "jeans" means Jeans (Men Denim Jeans for men); "formal wear" means \
the Men Formal line; "polos" means Men Casual Polos; "womenswear" means the Women line; "kidswear" \
means Boys, Girls, Little Boys and Little Girls.
- Targets are monthly. Pace is month-to-date: units sold so far vs what the target implies by \
now. Statuses: behind; drifting (slipping, but could still be normal ups and downs); on_pace; \
ahead; too_early (too few units expected to judge).

Rules:
1. Get every number from a tool before answering. Never estimate, recall or invent a figure. If \
a tool fails or has no data, say so plainly instead of guessing.
2. Keep actual numbers separate from projections, and label projections as such ("if the \
current rate continues..."). Never assume what a future delivery will contain: if past \
deliveries came without some sizes, say so, and don't promise the next one will fix it.
3. When a category is behind or drifting, investigate before answering: check its stock health \
(check_size_runs), its deliveries (get_stock_history), and visitors vs buyers \
(get_conversion_metrics). Name the cause: a stockout; a broken size run (core sizes gone while \
the shelf still looks full, which is different from a stockout); a traffic problem (fewer \
visitors); a conversion problem (normal visitors, fewer buying); or no clear cause. Don't \
overstate weak evidence such as "drifting", "possible_dip" or "too_few_sales_to_judge".
4. End every diagnosis with one concrete action the team can take on the floor today or \
tomorrow.
5. Cross-selling and loyalty targeting are at tier level only (Platinum, Gold, Silver, \
Non-member). Never reason about or refer to individual customers: that is a deliberate privacy \
choice, not a missing feature. When suggesting a cross-sell, name the tier and the offer, and \
phrase it as something an associate can say or do at the till.
6. For staffing, use get_staffing_recommendation and say which past days it is based on.
7. Be brief: a manager reads this between customers. Plain language, short paragraphs or a few \
bullets, no tables, no emoji, no analyst jargon (say "share of visitors who bought" rather than \
"conversion rate"). Refer to shoppers neutrally ("the shopper", "they").
8. Each question is answered on its own, without memory of earlier ones, so don't end with a \
question or an offer to do more."""


def _category(description="Category id, e.g. 'Women Tops'."):
    return {"type": "string", "enum": CATEGORIES, "description": description}


_LINE = {"type": "string", "enum": list(LINES), "description": "Line, e.g. 'Men Casual' or 'Women'."}
_ZONE = {"type": "string", "enum": DEPARTMENTS, "description": "Floor zone."}
_HOUR = {"type": "integer", "description": "Store hour slot (10-19). Omit for now."}


def _tool(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


TOOLS = [
    _tool("get_category_pace",
          "Month-to-date pace for all categories, or one category or line: units sold, expected by "
          "now, % vs pace, status, units per day needed vs actual, and a labelled month-end "
          "projection. Start here to see what needs attention.",
          {"category": _category(), "line": _LINE, "hour": _HOUR}),
    _tool("get_today_pace",
          "Today's sales so far vs what a typical day would have sold by now, per line.",
          {"line": _LINE, "hour": _HOUR}),
    _tool("get_contribution",
          "Month-to-date units and value by line and category, each as a share of the store "
          "(the store's contribution report).",
          {"hour": _HOUR}),
    _tool("get_stock_health_report",
          "Every category whose stock isn't healthy right now: stockouts, broken size runs, "
          "running out. Use for 'is anything low on stock?'."),
    _tool("check_size_runs",
          "Stock health verdict for one category: stockout, broken_size_run (core sizes gone "
          "while total stock still looks fine), running_out, or healthy, with the numbers.",
          {"category": _category()}, ["category"]),
    _tool("get_stock_status",
          "Units left by size for one category right now, which sizes are out, and the core sizes.",
          {"category": _category()}, ["category"]),
    _tool("get_stock_history",
          "One category's recent stock by day, every delivery received this month with the sizes "
          "in it, and scheduled deliveries that never arrived. Use to find the cause of a stock "
          "problem.",
          {"category": _category()}, ["category"]),
    _tool("get_last_piece_alerts",
          "Sizes that dropped to their last unit today, with the hour and whether it is a core size."),
    _tool("get_days_of_cover",
          "Projection of how many days each size of a category will last at its recent selling "
          "rate, and which are likely to run out before the next delivery.",
          {"category": _category()}, ["category"]),
    _tool("get_footfall",
          "Visitors to a floor zone today so far vs a typical day of the same kind, plus the last "
          "7 days. Give a zone, or a category or line to use its zone.",
          {"zone": _ZONE, "category": _category(), "line": _LINE, "hour": _HOUR}),
    _tool("get_conversion_metrics",
          "Visitors vs buyers for a category, line or zone: share of visitors who bought, units "
          "per transaction, and a reading of whether a slowdown is a traffic problem, a conversion "
          "problem, a possible dip, or too small to judge. Its 'recent' window is the last 3 days "
          "plus today (not 7 days), compared with the first half of the month.",
          {"category": _category(), "line": _LINE, "zone": _ZONE}),
    _tool("get_staffing_recommendation",
          "Tomorrow's peak hours per floor zone from past days of the same kind, a suggested floor "
          "team split at the busiest hour, the days it's based on, and delivery-day notes."),
    _tool("get_cross_sell_ideas",
          "Cross-sell ideas for categories behind pace, adapted to the cause (substitute for a "
          "stockout, available sizes for a broken size run, a complementary pairing otherwise), "
          "each naming a loyalty tier and offer, with any supply action."),
    _tool("get_tier_playbook",
          "Loyalty tiers for one category ranked by cross-sell response, with each tier's "
          "preferred offer phrased for the till. Tier-level data only.",
          {"category": _category()}, ["category"]),
]


def _build_tool_dispatch(current_hour, store):
    """
    Maps tool names to functions. Every tool works on the store's today and
    never sees past `current_hour`: an hour the model asks for is capped at
    now, so the agent can't read sales that haven't happened yet.
    """
    def at(hour):
        return current_hour if hour is None else min(int(hour), current_hour)

    day, now, s = store.today_day, current_hour, store
    return {
        "get_category_pace": lambda category=None, line=None, hour=None:
            kpi.get_category_pace(day, at(hour), category=category, line=line, store=s),
        "get_today_pace": lambda line=None, hour=None:
            kpi.get_today_pace(line=line, day=day, hour=at(hour), store=s),
        "get_contribution": lambda hour=None: kpi.get_contribution(day, at(hour), store=s),
        "get_stock_health_report": lambda: stock.get_stock_health_report(day, now, store=s),
        "check_size_runs": lambda category: stock.check_size_runs(category, day, now, store=s),
        "get_stock_status": lambda category: stock.get_stock_status(category, day, now, store=s),
        "get_stock_history": lambda category: stock.get_stock_history(category, day, now, store=s),
        "get_last_piece_alerts": lambda: stock.get_last_piece_alerts(day, now, store=s),
        "get_days_of_cover": lambda category: stock.get_days_of_cover(category, day, now, store=s),
        "get_footfall": lambda zone=None, category=None, line=None, hour=None:
            kpi.get_footfall(zone=zone, category=category, line=line, day=day, hour=at(hour), store=s),
        "get_conversion_metrics": lambda category=None, line=None, zone=None:
            kpi.get_conversion_metrics(category=category, line=line, zone=zone,
                                       day=day, hour=now, store=s),
        "get_staffing_recommendation": lambda: staffing.get_staffing_recommendation(day + 1, store=s),
        "get_cross_sell_ideas": lambda: loyalty.get_cross_sell_ideas(day, now, store=s),
        "get_tier_playbook": lambda category: loyalty.get_tier_playbook(category, store=s),
    }


def get_client():
    """
    Builds the API client for the configured provider, reading its key from
    the environment (never hardcoded). Raises AgentError with a friendly
    message if the key is missing.
    """
    # Imported lazily so the rest of the app runs even without the package.
    from anthropic import Anthropic

    api_key = (os.environ.get(PROVIDER["key_env"]) or "").strip()
    if not api_key:
        raise AgentError(
            f"No {PROVIDER['key_env']} found. Add your {PROVIDER['name']} API key to the .env "
            f"file (see .env.example), then restart the app."
        )
    if PROVIDER["base_url"]:
        return Anthropic(api_key=api_key, base_url=PROVIDER["base_url"])
    return Anthropic(api_key=api_key)


def ask(question, current_hour=None, client=None):
    """
    Sends a question to the model with tools enabled, runs the tool-call loop
    until it has a final answer, and returns (answer_text, tool_call_log).
    tool_call_log lists {"tool", "input", "result"} in call order, for the
    app's "How I got this" trace.

    Never raises for ordinary failures (missing key, no credit, network):
    those come back as a friendly answer with an empty trace.
    """
    from anthropic import APIError

    tool_call_log = []
    try:
        if client is None:
            client = get_client()
    except AgentError as e:
        return str(e), tool_call_log

    store, _, current_hour = resolve(hour=current_hour)  # the demo store
    dispatch = _build_tool_dispatch(current_hour, store)
    system = build_system_prompt(current_hour, store)
    messages = [{"role": "user", "content": question}]

    def call_model():
        return client.messages.create(
            model=PROVIDER["model"],
            max_tokens=AI_MAX_TOKENS,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

    try:
        response = call_model()
        rounds = 0
        while response.stop_reason == "tool_use" and rounds < MAX_TOOL_ITERATIONS:
            rounds += 1
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in (b for b in response.content if b.type == "tool_use"):
                try:
                    result = dispatch[block.name](**(block.input or {}))
                except Exception as e:  # a bad tool call shouldn't crash the app
                    result = {"error": f"Tool call failed: {e}"}
                tool_call_log.append({"tool": block.name, "input": block.input, "result": result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })

            # All results from one round go back together in a single message.
            messages.append({"role": "user", "content": tool_results})
            response = call_model()

        answer = "".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "tool_use":
            answer = (answer + "\n\n" if answer else "") + (
                "I ran out of lookups before finishing. Try asking a narrower question.")
        if not answer:
            answer = "I wasn't able to reach a final answer for that. Try rephrasing the question."
        return answer, tool_call_log

    except APIError as e:
        return _friendly_api_error(e), tool_call_log
    except Exception as e:
        return f"Something went wrong talking to {PROVIDER['name']}: {e}", tool_call_log


def _friendly_api_error(error):
    """Turns common API failures into a plain instruction instead of a raw error dump."""
    import anthropic

    name, console = PROVIDER["name"], PROVIDER["console"]
    status = getattr(error, "status_code", None)
    if isinstance(error, anthropic.AuthenticationError):
        return (f"The {name} API key in the .env file isn't being accepted. Check it was copied in "
                f"full from {console}, then restart the app.")
    if status == 402 or "credit balance" in str(error).lower() or "insufficient" in str(error).lower():
        return (f"The AI chat is out of {name} credit. Top up at {console}, then ask again. "
                f"Everything else on this page works without it.")
    if isinstance(error, anthropic.RateLimitError):
        return "Too many questions in a short time. Wait a minute and try again."
    if isinstance(error, anthropic.APIConnectionError):
        return f"Couldn't reach {name}. Check the internet connection and try again."
    return f"The {name} API returned an error, so no answer this time. Details: {error}"


def _terminal_loop():
    print(f"Category Pulse agent ({PROVIDER['name']}, model {PROVIDER['model']})")
    demo, day, hour = resolve()
    print(f"Day {day}, time {hour + 1}:00. Type 'quit' to exit.\n")
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
                print(f"  - {call['tool']}({call['input']})")
        print(f"\nAgent: {answer}\n")


if __name__ == "__main__":
    _terminal_loop()
