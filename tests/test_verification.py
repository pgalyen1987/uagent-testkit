"""Sender verification must behave the same under test as in production.

A handler without allow_unverified is only reachable from a verified agent address.
If the harness ignored that, tests would pass for a message flow that the real
framework rejects.
"""

import pytest
from uagents import Agent, Context

from uagent_testkit import ANY, UnverifiedSender, harness, user_sender

from .conftest import Ping, Pong


def build_agent(name: str, seed: str, *, allow_unverified: bool) -> Agent:
    agent = Agent(name=name, seed=seed)

    @agent.on_message(model=Ping, allow_unverified=allow_unverified)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        await ctx.send(sender, Pong(text="ok"))

    return agent


async def test_user_address_is_refused_by_a_verified_only_handler():
    h = harness(build_agent("strict", "strict agent seed", allow_unverified=False))

    with pytest.raises(UnverifiedSender, match="user address"):
        await h.deliver(Ping(text="hi"), sender=user_sender())


async def test_user_address_is_accepted_when_unverified_is_allowed():
    h = harness(build_agent("lax", "lax agent seed", allow_unverified=True))
    sender = user_sender()

    result = await h.deliver(Ping(text="hi"), sender=sender)

    assert result.reply(Pong).text == "ok"
    assert result.sent[0].destination == sender


async def test_agent_address_reaches_a_verified_only_handler():
    h = harness(build_agent("strict2", "strict2 agent seed", allow_unverified=False))

    result = await h.deliver(Ping(text="hi"), sender="agent1qverifiedpeer")

    assert result.reply(Pong).text == "ok"


async def test_any_matches_messages_to_third_parties():
    third_party = "agent1qthirdparty"
    agent = Agent(name="forwarder", seed="forwarder agent seed")

    @agent.on_message(model=Ping)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        await ctx.send(third_party, Pong(text="forwarded"))

    h = harness(agent)
    result = await h.deliver(Ping(text="hi"))

    assert result.replies(Pong) == []  # nothing went back to the sender
    assert result.replies(Pong, to=ANY)[0].text == "forwarded"
    assert result.replies(Pong, to=third_party)[0].text == "forwarded"
