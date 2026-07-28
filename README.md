# uagent-testkit

[![PyPI](https://img.shields.io/pypi/v/uagent-testkit.svg)](https://pypi.org/project/uagent-testkit/)
[![CI](https://github.com/pgalyen1987/fetchai_harness/actions/workflows/ci.yml/badge.svg)](https://github.com/pgalyen1987/fetchai_harness/actions/workflows/ci.yml)

Test [Fetch.ai uAgents](https://github.com/fetchai/uAgents) without a network, a wallet, or a `sleep()`.

```python
from uagent_testkit import harness

async def test_echo():
    h = harness(my_agent)
    result = await h.deliver(Ping(text="hi"))

    assert result.reply(Pong).text == "pong:hi"
    assert h.storage["seen"] == 1
```

## Why

The uAgents framework has no testing story. To exercise a handler today you generally
have to stand the agent up for real — which means Almanac registration, endpoint
resolution, a funded wallet on some paths, and `asyncio.sleep()` calls sprinkled through
the test to wait for messages that may never arrive. That is slow, flaky, and awkward to
run in CI.

Worse, `Agent._handle_message` catches every exception a handler raises and writes it to
a log. A handler that crashes on every message still looks like a green test. The most
common agent bug is invisible to the most common agent test.

`uagent-testkit` routes messages straight to the registered handler with a context that
records instead of transmitting. No sockets, no registration, no waiting.

- **Handler exceptions are raised**, not swallowed into a log.
- **Storage is in-memory**, so state doesn't leak between tests via the JSON file the
  stock `KeyValueStore` writes to your working directory.
- **Reply contracts are enforced** — if `@on_message(replies=Pong)` doesn't send a
  `Pong`, that's an assertion failure instead of a log line nobody reads.
- **Interval and lifecycle handlers run on demand**, so an `@on_interval(period=3600)`
  is testable without waiting an hour.
- **Multi-agent conversations replay deterministically**, with a loop guard instead of
  a hung test.

## Install

```bash
pip install uagent-testkit
```

Requires Python 3.10+ and `uagents>=0.22`. The pytest fixtures register automatically.

## Testing one agent

```python
from uagents import Agent, Context, Model
from uagent_testkit import harness

class Ping(Model):
    text: str

class Pong(Model):
    text: str

agent = Agent(name="echo", seed="echo seed")

@agent.on_message(model=Ping, replies=Pong)
async def on_ping(ctx: Context, sender: str, msg: Ping):
    ctx.storage.set("seen", (ctx.storage.get("seen") or 0) + 1)
    await ctx.send(sender, Pong(text=f"pong:{msg.text}"))
```

```python
async def test_ping():
    h = harness(agent)
    result = await h.deliver(Ping(text="hi"), sender="agent1qexample")

    result.assert_replied_with(Pong)
    result.assert_reply_contract()          # honoured its replies= declaration
    assert result.reply(Pong).text == "pong:hi"
    assert result.sent[0].destination == "agent1qexample"
    assert h.storage["seen"] == 1
```

### Asserting on failure

```python
async def test_bad_input_is_handled():
    result = await h.deliver(Ping(text=""), raise_errors=False)
    assert isinstance(result.error, ValueError)
    assert "empty" in result.logs.errors[0]
```

By default `deliver()` re-raises whatever the handler raised, so a broken handler fails
the test loudly. Pass `raise_errors=False` when the failure is the thing under test.

### Sender verification

Handlers without `allow_unverified=True` are only reachable from a verified agent
address. The harness enforces that, so a test can't pass for a flow production
rejects:

```python
from uagent_testkit import UnverifiedSender, user_sender

async def test_strict_handler_refuses_users():
    with pytest.raises(UnverifiedSender):
        await h.deliver(Ping(text="hi"), sender=user_sender())
```

### Intervals and lifecycle

```python
await h.startup()                  # runs @on_event("startup") handlers
result = await h.tick()            # runs @on_interval handlers once, ignoring period
result = await h.tick(only="heartbeat")   # just one, by function name
await h.shutdown()
```

## Testing agents against each other

```python
from uagent_testkit import AgentNetwork

async def test_conversation():
    net = AgentNetwork(alice, bob)
    transcript = await net.send(Ping(text="hello"), to=bob, sender=alice)

    transcript.assert_delivered(Pong, to=alice.address)
    assert transcript.of(Pong)[0].text == "pong:hello"
    assert net.harness(alice).storage["last_pong"] == "pong:hello"
```

Every reply addressed to another agent in the network is delivered in turn until the
conversation goes quiet. Messages sent to addresses outside the network are collected on
`transcript.undelivered` rather than silently dropped:

```python
transcript.assert_all_delivered()   # fails if anything went to an unknown address
```

Agents that answer each other forever raise `ConversationTooLong` after `max_rounds`
deliveries instead of hanging the suite. This catches echo loops, which are easy to
write and expensive to discover in production:

```python
# a keyword responder whose answer contains its own trigger word
if "menu" in msg.text().lower():
    await ctx.send(sender, chat("here is the menu"))   # -> contains "menu"
```

Two agents like that will talk to each other indefinitely. On a live network that is
two agents spamming each other; here it fails in milliseconds with the transcript
so far attached.

## Protocols and the chat protocol

Agents assembled from `Protocol` objects work the same way, including the shared chat
protocol from `uagents_core` that Agentverse and Agent Launch agents use:

```python
chat = Protocol(spec=chat_protocol_spec)

@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    ...

agent.include(chat)
```

```python
async def test_chat():
    h = harness(agent)
    result = await h.deliver(
        ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=uuid4(),
            content=[TextContent(type="text", text="show me the menu")],
        )
    )
    assert result.replies(ChatAcknowledgement)
    assert result.replies(ChatMessage)[0].text() == "here is what we have"
```

## pytest fixtures

Installing the package registers two factory fixtures:

```python
def test_with_fixture(agent_harness, agent_network):
    h = agent_harness(my_agent)
    net = agent_network(alice, bob)
```

## API

| | |
|---|---|
| `harness(agent)` | Wrap an agent. Swaps in in-memory storage and merges the internal protocol. |
| `h.deliver(msg, sender=…, raise_errors=True)` | Route a message to its handler. Returns `Delivery`. |
| `h.tick(only=None)` | Run `@on_interval` handlers once. |
| `h.startup()` / `h.shutdown()` | Run `@on_event` handlers. |
| `h.storage` | In-memory store, subscriptable. |
| `h.handles(Model)` | Whether a handler is registered for a type. |
| `Delivery.sent` | List of `Sent(destination, schema_digest, body)`. |
| `Delivery.reply(Model)` | The single reply of that type; raises otherwise. |
| `Delivery.replies(Model, to=…)` | Replies of that type to the sender; `to=ANY` for any destination. |
| `user_sender()` | A user (non-agent) address, for `allow_unverified` paths. |
| `Delivery.error` / `.logs` | Handler exception and captured log records. |
| `Delivery.assert_replied_with` / `assert_silent` / `assert_no_errors` / `assert_reply_contract` | Chainable assertions. |
| `AgentNetwork(*agents)` | Multi-agent network. |
| `net.send(msg, to=…, sender=…, max_rounds=20)` | Play out a conversation. Returns `Transcript`. |
| `Transcript.of(Model)` / `.to(addr)` / `.undelivered` | Inspect what was exchanged. |

## Scope

This exercises your handler logic: routing, replies, storage, protocol contracts, and
inter-agent flow. It deliberately does not simulate Almanac resolution, envelope
signing, or on-chain settlement — those are integration concerns and mocking them would
give you false confidence. Keep a thin integration test on a real testnet for those, and
use this for everything else.

## Running the tests

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest
```

## License

Apache-2.0
