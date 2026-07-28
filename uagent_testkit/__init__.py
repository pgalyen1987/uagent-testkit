"""uagent-testkit — test Fetch.ai uAgents without a network, a wallet, or a sleep().

    from uagent_testkit import harness

    h = harness(my_agent)
    result = await h.deliver(Ping(text="hi"))
    assert result.reply(Pong).text == "pong:hi"
"""

from ._loop import ensure_event_loop
from ._recording import Sent
from ._storage import InMemoryStore
from .chain import (
    FET_BSC,
    ChainDouble,
    NetworkCallBlocked,
    UnstubbedCall,
)
from .harness import (
    ANY,
    DEFAULT_SENDER,
    AgentHarness,
    Delivery,
    HandlerNotFound,
    UnverifiedSender,
    harness,
    user_sender,
)
from .network import (
    AgentNetwork,
    ConversationTooLong,
    Exchange,
    Transcript,
)

__all__ = [
    "ANY",
    "AgentHarness",
    "AgentNetwork",
    "ChainDouble",
    "ConversationTooLong",
    "DEFAULT_SENDER",
    "Delivery",
    "Exchange",
    "FET_BSC",
    "HandlerNotFound",
    "InMemoryStore",
    "NetworkCallBlocked",
    "Sent",
    "Transcript",
    "UnstubbedCall",
    "UnverifiedSender",
    "ensure_event_loop",
    "harness",
    "user_sender",
]

__version__ = "0.1.1"
