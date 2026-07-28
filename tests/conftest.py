import pytest
from uagents import Agent, Context, Model


class Ping(Model):
    text: str


class Pong(Model):
    text: str


class Unhandled(Model):
    value: int


class Tick(Model):
    n: int


def build_echo_agent(name: str = "echo", seed: str = "echo agent test seed") -> Agent:
    agent = Agent(name=name, seed=seed)

    @agent.on_message(model=Ping, replies=Pong)
    async def on_ping(ctx: Context, sender: str, msg: Ping):
        count = (ctx.storage.get("seen") or 0) + 1
        ctx.storage.set("seen", count)
        await ctx.send(sender, Pong(text=f"pong:{msg.text}"))

    return agent


@pytest.fixture
def echo_agent() -> Agent:
    return build_echo_agent()
