"""
Checks the chat's plumbing, with no AI and no network:

  - the answer reaches the page as it's written, and a note written before a
    lookup is cleared
  - it asks for the same diagnosis the category pop-up shows
  - a stalled provider gets a plain message, marked so no question is used up,
    instead of an endless wait

Run from the project folder:  python tests/test_chat_flow.py
"""

import os
import sys
import warnings
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")

import anthropic  # noqa: E402

import agent  # noqa: E402
import diagnosis  # noqa: E402
from store import resolve  # noqa: E402


def text_block(text):
    return SimpleNamespace(type="text", text=text)


class FakeStream:
    def __init__(self, pieces, message):
        self.text_stream, self.message = pieces, message

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    """Answers each call from a script: (text pieces, content blocks, stop reason), or an error to raise."""

    def __init__(self, script):
        self.script, self.timeouts = list(script), []
        self.messages = self

    def with_options(self, timeout=None):
        self.timeouts.append(timeout)
        return self

    def stream(self, **_):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        pieces, content, stop = step
        return FakeStream(pieces, SimpleNamespace(content=content, stop_reason=stop))


def test_streams_and_clears_notes():
    store, day, hour = resolve()
    lookup = SimpleNamespace(type="tool_use", id="1", name="get_category_diagnosis",
                             input={"category": "Men Formal Blazers"})
    client = FakeClient([(["Let me check."], [text_block("Let me check."), lookup], "tool_use"),
                         (["It's a ", "slow seller."], [text_block("It's a slow seller.")], "end_turn")])
    seen = []
    answer, calls = agent.ask("Why?", current_hour=hour, client=client, store=store, on_text=seen.append)
    assert answer == "It's a slow seller." and not isinstance(answer, agent.NoAnswer)
    assert seen == ["Let me check.", None, "It's a ", "slow seller."], seen
    assert all(0 < t <= agent.AI_WAIT_SECONDS for t in client.timeouts), client.timeouts
    finding = diagnosis.diagnose("Men Formal Blazers", day, hour, store=store)
    assert calls[0]["result"]["cause"] == finding["cause"] and calls[0]["result"]["headline"] == finding["headline"]
    assert calls[0]["result"]["status"] == "Watch"
    print("The answer arrives as it's written, from the same diagnosis as the pop-up.")


def test_a_stall_gets_a_plain_message():
    store, _, hour = resolve()
    client = FakeClient([anthropic.APITimeoutError(request=None)])
    answer, calls = agent.ask("Why?", current_hour=hour, client=client, store=store)
    assert isinstance(answer, agent.NoAnswer) and "stopped waiting" in answer and calls == []
    print(f"A stalled provider: {answer}")


def test_every_tool_is_offered_and_works():
    store, day, hour = resolve()
    names = {t["name"] for t in agent.build_tools(store)}
    assert "get_category_diagnosis" in names
    assert names <= set(agent._build_tool_dispatch(hour, store))
    print(f"All {len(names)} tools have something to answer them.")


if __name__ == "__main__":
    test_streams_and_clears_notes()
    test_a_stall_gets_a_plain_message()
    test_every_tool_is_offered_and_works()
    print("\nAll checks passed.")
