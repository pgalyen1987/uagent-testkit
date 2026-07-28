import pytest
from uagents import Agent, Context, Model

from uagent_testkit import DEFAULT_SENDER, HandlerNotFound, harness

from .conftest import Ping, Pong, Tick, Unhandled, build_echo_agent


async def test_handler_reply_is_captured(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))

    assert result.reply(Pong).text == "pong:hi"
    result.assert_replied_with(Pong).assert_no_errors()


async def test_reply_goes_to_the_sender(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"), sender="agent1qtestsender")

    assert result.sent[0].destination == "agent1qtestsender"
    assert result.replies(Pong, to="agent1qtestsender")


async def test_default_sender_is_a_real_agent_address(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))

    assert DEFAULT_SENDER.startswith("agent1q")
    assert result.sent[0].destination == DEFAULT_SENDER


async def test_storage_is_readable_and_isolated_per_harness():
    a = harness(build_echo_agent(seed="storage seed a"))
    b = harness(build_echo_agent(name="echo2", seed="storage seed b"))

    await a.deliver(Ping(text="one"))
    await a.deliver(Ping(text="two"))
    await b.deliver(Ping(text="one"))

    assert a.storage["seen"] == 2
    assert b.storage["seen"] == 1


async def test_storage_does_not_touch_disk(tmp_path, monkeypatch, echo_agent):
    """The stock KeyValueStore writes JSON to cwd; the harness must not."""
    monkeypatch.chdir(tmp_path)
    h = harness(echo_agent)
    await h.deliver(Ping(text="hi"))

    assert h.storage["seen"] == 1
    assert list(tmp_path.iterdir()) == []


async def test_handler_exception_is_raised_not_swallowed():
    agent = Agent(name="boom", seed="boom agent seed")

    @agent.on_message(model=Ping)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        raise ValueError("handler is broken")

    h = harness(agent)
    with pytest.raises(ValueError, match="handler is broken"):
        await h.deliver(Ping(text="hi"))


async def test_handler_exception_can_be_inspected_instead():
    agent = Agent(name="boom2", seed="boom2 agent seed")

    @agent.on_message(model=Ping)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        raise ValueError("handler is broken")

    h = harness(agent)
    result = await h.deliver(Ping(text="hi"), raise_errors=False)

    assert isinstance(result.error, ValueError)
    with pytest.raises(AssertionError):
        result.assert_no_errors()


async def test_unknown_message_type_is_reported():
    h = harness(build_echo_agent(seed="unknown seed"))

    assert not h.handles(Unhandled)
    with pytest.raises(HandlerNotFound):
        await h.deliver(Unhandled(value=1))


async def test_assert_silent():
    agent = Agent(name="quiet", seed="quiet agent seed")

    @agent.on_message(model=Ping)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        ctx.storage.set("noted", msg.text)

    h = harness(agent)
    result = await h.deliver(Ping(text="hi"))

    result.assert_silent()
    assert h.storage["noted"] == "hi"


async def test_reply_contract_violation_is_caught():
    """@on_message(replies=Pong) but the handler never sends one."""
    agent = Agent(name="rude", seed="rude agent seed")

    @agent.on_message(model=Ping, replies=Pong)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        pass  # forgot to reply

    h = harness(agent)
    result = await h.deliver(Ping(text="hi"))

    with pytest.raises(AssertionError, match="declares replies"):
        result.assert_reply_contract()


async def test_reply_contract_passes_when_honoured(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))
    result.assert_reply_contract()


async def test_reply_raises_when_missing(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))

    with pytest.raises(AssertionError, match="expected one Ping"):
        result.reply(Ping)


async def test_wrong_model_parse_is_rejected(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))

    with pytest.raises(AssertionError, match="is not a Ping"):
        result.sent[0].parse(Ping)


async def test_interval_handler_runs_once_without_waiting():
    agent = Agent(name="ticker", seed="ticker agent seed")
    peer = "agent1qpeer"

    @agent.on_interval(period=3600.0, messages=Tick)
    async def beat(ctx: Context):
        n = (ctx.storage.get("n") or 0) + 1
        ctx.storage.set("n", n)
        await ctx.send(peer, Tick(n=n))

    h = harness(agent)
    result = await h.tick()

    assert h.storage["n"] == 1
    assert result.replies(Tick, to=peer)[0].n == 1


async def test_tick_can_select_one_handler():
    agent = Agent(name="two-ticks", seed="two ticks agent seed")

    @agent.on_interval(period=1.0)
    async def first(ctx: Context):
        ctx.storage.set("first", True)

    @agent.on_interval(period=1.0)
    async def second(ctx: Context):
        ctx.storage.set("second", True)

    h = harness(agent)
    await h.tick(only="second")

    assert "first" not in h.storage
    assert h.storage["second"] is True

    with pytest.raises(HandlerNotFound):
        await h.tick(only="nope")


async def test_startup_and_shutdown_handlers():
    agent = Agent(name="lifecycle", seed="lifecycle agent seed")

    @agent.on_event("startup")
    async def up(ctx: Context):
        ctx.storage.set("state", "up")

    @agent.on_event("shutdown")
    async def down(ctx: Context):
        ctx.storage.set("state", "down")

    h = harness(agent)

    await h.startup()
    assert h.storage["state"] == "up"

    await h.shutdown()
    assert h.storage["state"] == "down"


async def test_harness_is_idempotent_over_repeated_wrapping(echo_agent):
    """Wrapping twice must not blow up on duplicate protocol inclusion."""
    harness(echo_agent)
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))
    assert result.reply(Pong).text == "pong:hi"


async def test_session_is_stable_within_a_delivery(echo_agent):
    h = harness(echo_agent)
    result = await h.deliver(Ping(text="hi"))
    assert result.session is not None
