"""Agents that read BNB Chain, tested offline.

These drive real HTTP clients (requests / aiohttp) so the patching is exercised the
way an actual agent would exercise it, not through a hand-rolled shim.
"""

import pytest
from uagents import Agent, Context, Model

from uagent_testkit import (
    FET_BSC,
    ChainDouble,
    NetworkCallBlocked,
    UnstubbedCall,
    harness,
)

RPC = "https://bsc-dataseed.binance.org/"
WALLET = "0x8E57BFDE053dBb6862991759c19affC5F383d5D0"


def _rpc(method, params):
    import requests

    return requests.Session().request(
        "POST", RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).json()


class CheckBalance(Model):
    wallet: str


class BalanceReport(Model):
    symbol: str
    amount: float


def build_treasury_agent(seed: str) -> Agent:
    """An agent whose whole job is reading a BEP-20 balance off BNB Chain."""
    agent = Agent(name="treasury", seed=seed)

    @agent.on_message(model=CheckBalance, replies=BalanceReport)
    async def on_check(ctx: Context, sender: str, msg: CheckBalance):
        import requests

        session = requests.Session()

        def call(data):
            return session.request(
                "POST",
                RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": FET_BSC, "data": data}, "latest"],
                },
            ).json()["result"]

        raw = int(call("0x70a08231" + "0" * 24 + msg.wallet[2:].lower()), 16)
        decimals = int(call("0x313ce567"), 16)
        symbol_hex = call("0x95d89b41")
        length = int(symbol_hex[2 + 64 : 2 + 128], 16)
        symbol = bytes.fromhex(symbol_hex[2 + 128 : 2 + 128 + length * 2]).decode()

        ctx.storage.set("last_wallet", msg.wallet)
        await ctx.send(
            sender, BalanceReport(symbol=symbol, amount=raw / 10**decimals)
        )

    return agent


async def test_agent_reads_a_bep20_balance_offline():
    chain = ChainDouble()
    chain.add_token(
        FET_BSC, symbol="FET", decimals=18, balances={WALLET: 5_500_000_000_000_000_000}
    )
    h = harness(build_treasury_agent("treasury seed one"))

    with chain.install():
        result = await h.deliver(CheckBalance(wallet=WALLET))

    report = result.reply(BalanceReport)
    assert report.symbol == "FET"
    assert report.amount == 5.5
    chain.assert_called("eth_call")


async def test_native_bnb_balance():
    chain = ChainDouble().set_balance(WALLET, 2 * 10**18)

    with chain.install():
        got = _rpc("eth_getBalance", [WALLET, "latest"])

    assert int(got["result"], 16) == 2 * 10**18


async def test_chain_metadata():
    chain = ChainDouble().set_block_number(41_234_567)

    with chain.install():
        assert int(_rpc("eth_chainId", [])["result"], 16) == 56
        assert int(_rpc("eth_blockNumber", [])["result"], 16) == 41_234_567


async def test_unstubbed_balance_raises_rather_than_returning_zero():
    """A silent zero would let a test pass on data nobody set up."""
    chain = ChainDouble()

    with chain.install():
        with pytest.raises(UnstubbedCall, match="never set"):
            _rpc("eth_getBalance", [WALLET, "latest"])


async def test_unregistered_contract_raises():
    chain = ChainDouble()

    with chain.install():
        with pytest.raises(UnstubbedCall, match="never registered"):
            _rpc("eth_call", [{"to": FET_BSC, "data": "0x313ce567"}, "latest"])


async def test_unstubbed_holder_balance_raises():
    chain = ChainDouble()
    chain.add_token(FET_BSC, symbol="FET", balances={})

    with chain.install():
        with pytest.raises(UnstubbedCall, match="balance of"):
            _rpc(
                "eth_call",
                [
                    {"to": FET_BSC, "data": "0x70a08231" + "0" * 24 + WALLET[2:].lower()},
                    "latest",
                ],
            )


async def test_non_rpc_http_is_blocked():
    """An agent calling some other API mid-handler should fail the test, not the CI run."""
    import requests

    chain = ChainDouble()

    with chain.install():
        with pytest.raises(NetworkCallBlocked, match="non-JSON-RPC"):
            requests.Session().request("GET", "https://example.com/prices")


async def test_web3_style_data_body_is_understood():
    """web3.py posts a JSON-encoded body via data=, not json=."""
    import json

    import requests

    chain = ChainDouble().set_balance(WALLET, 7)

    with chain.install():
        resp = requests.Session().request(
            "POST",
            RPC,
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                 "params": [WALLET, "latest"]}
            ),
        )

    assert int(resp.json()["result"], 16) == 7


async def test_batch_requests():
    chain = ChainDouble().set_balance(WALLET, 3)

    with chain.install():
        import requests

        resp = requests.Session().request(
            "POST",
            RPC,
            json=[
                {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                {"jsonrpc": "2.0", "id": 2, "method": "eth_getBalance",
                 "params": [WALLET, "latest"]},
            ],
        )

    body = resp.json()
    assert len(body) == 2
    assert int(body[1]["result"], 16) == 3


async def test_aiohttp_client_is_patched():
    import aiohttp

    chain = ChainDouble().set_balance(WALLET, 11)

    with chain.install():
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RPC,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                      "params": [WALLET, "latest"]},
            ) as resp:
                body = await resp.json()

    assert int(body["result"], 16) == 11


async def test_patches_are_removed_on_exit():
    """Leaving the context must restore the real client, or later tests get poisoned."""
    import requests

    original = requests.Session.request
    with ChainDouble().install():
        assert requests.Session.request is not original
    assert requests.Session.request is original


async def test_assert_no_calls():
    chain = ChainDouble()
    with chain.install():
        pass
    chain.assert_no_calls()

    with pytest.raises(AssertionError, match="expected a eth_call call"):
        chain.assert_called("eth_call")
