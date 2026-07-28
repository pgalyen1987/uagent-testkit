"""Agents assembled from Protocol objects, including the shared chat protocol.

Agent Launch agents are typically built by including a `Protocol(spec=chat_protocol_spec)`
rather than by decorating the agent directly. That path has to work, so it is
covered here against the real protocol shipped in uagents_core.
"""

from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

import pytest

from uagent_testkit import AgentNetwork, ConversationTooLong, harness


def _chat(*content) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=list(content)
    )


class WordCountRequest(Model):
    text: str


class WordCountResponse(Model):
    count: int


def build_word_counter(seed: str) -> Agent:
    """Mirrors the framework's own 44-bureau-chat-agents example."""
    agent = Agent(name="counter", seed=seed)
    proto = Protocol(name="WordCounter", version="0.1.0")

    @proto.on_message(WordCountRequest, replies=WordCountResponse)
    async def handle(ctx: Context, sender: str, msg: WordCountRequest):
        await ctx.send(
            sender,
            WordCountResponse(count=len([w for w in msg.text.split() if w.strip()])),
        )

    agent.include(proto)
    return agent


def build_chat_agent(seed: str, *, answer: str = "here is what we have") -> Agent:
    agent = Agent(name="chatter", seed=seed)
    chat = Protocol(spec=chat_protocol_spec)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(
            sender,
            ChatAcknowledgement(
                timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id
            ),
        )
        text = msg.text()
        ctx.storage.set("heard", text)
        if "menu" in text.lower():
            await ctx.send(sender, _chat(TextContent(type="text", text=answer)))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.storage.set("acked", str(msg.acknowledged_msg_id))

    agent.include(chat)
    return agent


async def test_protocol_included_handlers_are_reachable():
    h = harness(build_word_counter("word counter seed"))

    result = await h.deliver(WordCountRequest(text="one two three"))

    assert result.reply(WordCountResponse).count == 3
    result.assert_reply_contract()


async def test_chat_message_is_acknowledged():
    h = harness(build_chat_agent("chat agent seed"))

    result = await h.deliver(_chat(TextContent(type="text", text="hello there")))

    assert result.replies(ChatAcknowledgement)
    assert h.storage["heard"] == "hello there"


async def test_chat_agent_answers_a_keyword():
    h = harness(build_chat_agent("chat keyword seed"))

    result = await h.deliver(_chat(TextContent(type="text", text="show me the MENU")))

    replies = result.replies(ChatMessage)
    assert replies[0].text() == "here is what we have"


async def test_non_text_content_is_handled():
    """StartSessionContent carries no text; text() must yield '' not blow up."""
    h = harness(build_chat_agent("chat session seed"))

    result = await h.deliver(_chat(StartSessionContent(type="start-session")))

    assert h.storage["heard"] == ""
    assert result.replies(ChatAcknowledgement)


async def test_mixed_content_concatenates_text():
    h = harness(build_chat_agent("chat mixed seed"))

    await h.deliver(
        _chat(
            StartSessionContent(type="start-session"),
            TextContent(type="text", text="menu "),
            TextContent(type="text", text="please"),
            EndSessionContent(type="end-session"),
        )
    )

    assert h.storage["heard"] == "menu please"


async def test_two_chat_agents_exchange_and_acknowledge():
    a = build_chat_agent("chat pair a seed")
    b = build_chat_agent("chat pair b seed")
    net = AgentNetwork(a, b)

    transcript = await net.send(
        _chat(TextContent(type="text", text="menu")), to=b, sender=a
    )

    transcript.assert_delivered(ChatMessage, to=b.address)
    transcript.assert_delivered(ChatAcknowledgement, to=a.address)
    assert net.harness(a).storage["heard"] == "here is what we have"


async def test_echo_loop_between_chat_agents_is_caught():
    """A keyword responder whose answer contains its own trigger word.

    Two of these will talk to each other forever. On a live network that is two
    agents spamming indefinitely; here it fails in milliseconds with the
    conversation so far attached.
    """
    a = build_chat_agent("echo loop a seed", answer="here is the menu")
    b = build_chat_agent("echo loop b seed", answer="here is the menu")
    net = AgentNetwork(a, b)

    with pytest.raises(ConversationTooLong) as exc:
        await net.send(_chat(TextContent(type="text", text="menu")), to=b, sender=a)

    assert "reply loop" in str(exc.value)
