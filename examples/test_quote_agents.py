"""The same flow, tested end to end.

Every test here executes in under 5ms; the only real cost in the file is deriving
the agents' keys from their seeds once at import.
"""

from uagent_testkit import AgentNetwork, harness

from .quote_agents import (
    Quote,
    QuoteRequest,
    QuoteUnavailable,
    oracle,
    trader,
)


async def test_oracle_answers_a_known_symbol():
    h = harness(oracle)
    result = await h.deliver(QuoteRequest(symbol="FET"))

    assert result.reply(Quote).price == 1.42
    result.assert_reply_contract()
    assert h.storage["served"] == 1


async def test_oracle_reports_an_unknown_symbol():
    h = harness(oracle)
    result = await h.deliver(QuoteRequest(symbol="DOGE"))

    assert result.reply(QuoteUnavailable).reason == "symbol not tracked"
    assert h.storage["misses"] == ["DOGE"]


async def test_symbols_are_normalised():
    h = harness(oracle)
    result = await h.deliver(QuoteRequest(symbol="bnb"))

    assert result.reply(Quote).symbol == "BNB"


async def test_full_round_trip_between_two_agents():
    net = AgentNetwork(trader, oracle)
    transcript = await net.send(
        QuoteRequest(symbol="FET"), to=oracle, sender=trader
    )

    transcript.assert_delivered(Quote, to=trader.address)
    transcript.assert_all_delivered()
    assert net.harness(trader).storage["last_price"] == 1.42


async def test_failure_path_reaches_the_trader():
    net = AgentNetwork(trader, oracle)
    transcript = await net.send(
        QuoteRequest(symbol="NOPE"), to=oracle, sender=trader
    )

    transcript.assert_delivered(QuoteUnavailable, to=trader.address)
    assert net.harness(trader).storage["last_error"] == "symbol not tracked"


async def test_the_minute_poll_without_waiting_a_minute():
    h = harness(trader)
    result = await h.tick(only="poll_price")

    assert result.reply(QuoteRequest, to=oracle.address).symbol == "FET"
