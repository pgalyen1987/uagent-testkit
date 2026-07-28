"""pytest fixtures. Enabled automatically once the package is installed."""

from __future__ import annotations

import pytest

from ._loop import ensure_event_loop
from .harness import AgentHarness, harness as _harness
from .network import AgentNetwork


def pytest_configure(config: pytest.Config) -> None:
    """Install an event loop before collection.

    Test modules routinely define `agent = Agent(...)` at module scope, which needs
    a current event loop at import time.
    """
    ensure_event_loop()


@pytest.fixture(autouse=True)
def _uagent_event_loop():
    """Re-establish a loop for each test.

    pytest-asyncio closes its loop after every test; without this, a synchronous
    fixture that constructs an Agent fails from the second test onward.
    """
    ensure_event_loop()
    yield


@pytest.fixture
def agent_harness():
    """Factory fixture wrapping an Agent for testing.

        def test_reply(agent_harness):
            h = agent_harness(my_agent)
    """

    def _factory(agent, *, persist_storage: bool = False) -> AgentHarness:
        return _harness(agent, persist_storage=persist_storage)

    return _factory


@pytest.fixture
def agent_network():
    """Factory fixture building an AgentNetwork from agents or harnesses.

        def test_conversation(agent_network):
            net = agent_network(alice, bob)
    """

    def _factory(*agents, persist_storage: bool = False) -> AgentNetwork:
        return AgentNetwork(*agents, persist_storage=persist_storage)

    return _factory
