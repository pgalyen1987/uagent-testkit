"""Proves the harness performs no I/O.

The whole premise is that handlers can be exercised without a network. Rather than
claim that, this blocks socket connections outright and runs the full surface —
delivery, replies, intervals, lifecycle and a two-agent conversation — underneath.
"""

import socket

import pytest
from uagents import Agent, Context

from uagent_testkit import AgentNetwork, harness

from .conftest import Ping, Pong, Tick, build_echo_agent


class NetworkAccessAttempted(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise NetworkAccessAttempted("the harness attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    return blocked


async def test_delivery_makes_no_connections(no_network):
    h = harness(build_echo_agent(seed="offline echo seed"))
    result = await h.deliver(Ping(text="offline"))

    assert result.reply(Pong).text == "pong:offline"


async def test_agent_construction_and_intervals_make_no_connections(no_network):
    agent = Agent(name="offline-ticker", seed="offline ticker seed")

    @agent.on_interval(period=900.0, messages=Tick)
    async def beat(ctx: Context):
        await ctx.send("agent1qsomepeer", Tick(n=1))

    @agent.on_event("startup")
    async def up(ctx: Context):
        ctx.storage.set("ready", True)

    h = harness(agent)
    await h.startup()
    result = await h.tick()

    assert h.storage["ready"] is True
    assert result.replies(Tick, to="agent1qsomepeer")[0].n == 1


async def test_multi_agent_conversation_makes_no_connections(no_network):
    a = Agent(name="offline-a", seed="offline a seed")
    b = build_echo_agent(name="offline-b", seed="offline b seed")

    @a.on_message(model=Pong)
    async def on_pong(ctx: Context, sender: str, msg: Pong):
        ctx.storage.set("got", msg.text)

    net = AgentNetwork(a, b)
    transcript = await net.send(Ping(text="quiet"), to=b, sender=a)

    transcript.assert_delivered(Pong, to=a.address)
    assert net.harness(a).storage["got"] == "pong:quiet"
