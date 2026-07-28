import pytest
from uagents import Agent, Context, Model

from uagent_testkit import AgentNetwork, ConversationTooLong

from .conftest import Ping, Pong, build_echo_agent


class Ack(Model):
    ok: bool


def build_pinger(peer_address_holder: dict) -> Agent:
    """An agent that answers Pong with an Ack, closing the conversation."""
    agent = Agent(name="pinger", seed="pinger agent seed")

    @agent.on_message(model=Pong, replies=Ack)
    async def on_pong(ctx: Context, sender: str, msg: Pong):
        ctx.storage.set("last_pong", msg.text)
        await ctx.send(sender, Ack(ok=True))

    peer_address_holder["pinger"] = agent.address
    return agent


async def test_two_agents_hold_a_conversation():
    holder: dict = {}
    pinger = build_pinger(holder)
    echo = build_echo_agent(seed="network echo seed")

    net = AgentNetwork(pinger, echo)

    # echo replies Pong -> pinger replies Ack -> echo has no Ack handler, so it stops
    transcript = await net.send(Ping(text="hello"), to=echo, sender=pinger)

    transcript.assert_delivered(Ping, to=echo.address)
    transcript.assert_delivered(Pong, to=pinger.address)
    assert net.harness(pinger).storage["last_pong"] == "pong:hello"


async def test_transcript_records_order_and_payloads():
    holder: dict = {}
    pinger = build_pinger(holder)
    echo = build_echo_agent(seed="order echo seed")
    net = AgentNetwork(pinger, echo)

    transcript = await net.send(Ping(text="abc"), to=echo, sender=pinger)

    assert [type(m).__name__ for m in transcript.of(Ping)] == ["Ping"]
    assert transcript.of(Pong)[0].text == "pong:abc"
    assert len(transcript) >= 2


async def test_message_to_unknown_agent_is_reported_not_dropped():
    stranger = "agent1qstrangeraddressnotinnetwork"
    agent = Agent(name="talker", seed="talker agent seed")

    @agent.on_message(model=Ping)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        await ctx.send(stranger, Pong(text="into the void"))

    net = AgentNetwork(agent)
    transcript = await net.send(Ping(text="hi"), to=agent)

    assert transcript.undelivered
    assert transcript.undelivered[0].destination == stranger
    with pytest.raises(AssertionError, match="unknown addresses"):
        transcript.assert_all_delivered()


async def test_reply_loop_is_caught_rather_than_hanging():
    """Two agents that answer each other forever must fail fast, not spin."""
    a = Agent(name="loop-a", seed="loop a agent seed")
    b = Agent(name="loop-b", seed="loop b agent seed")

    @a.on_message(model=Pong)
    async def a_on_pong(ctx: Context, sender: str, msg: Pong):
        await ctx.send(sender, Ping(text="again"))

    @b.on_message(model=Ping)
    async def b_on_ping(ctx: Context, sender: str, msg: Ping):
        await ctx.send(sender, Pong(text="again"))

    net = AgentNetwork(a, b)

    with pytest.raises(ConversationTooLong):
        await net.send(Ping(text="start"), to=b, sender=a, max_rounds=6)


async def test_network_exposes_each_harness():
    echo = build_echo_agent(seed="lookup echo seed")
    net = AgentNetwork(echo)

    assert echo.address in net
    assert net.harness(echo).address == echo.address
    assert net.harness(echo.address).address == echo.address

    with pytest.raises(KeyError):
        net.harness("agent1qnotamember")


async def test_delivery_objects_are_available_per_exchange():
    holder: dict = {}
    pinger = build_pinger(holder)
    echo = build_echo_agent(seed="per exchange echo seed")
    net = AgentNetwork(pinger, echo)

    transcript = await net.send(Ping(text="x"), to=echo, sender=pinger)

    first = transcript.exchanges[0]
    assert first.recipient == echo.address
    first.delivery.assert_replied_with(Pong)
