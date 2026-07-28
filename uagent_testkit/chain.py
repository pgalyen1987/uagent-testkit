"""An offline stand-in for BNB Chain, for agents that read on-chain state.

The rest of this package intercepts `ctx.send`, which covers agent-to-agent messaging.
It does not cover a handler that reaches out to an RPC endpoint — an agent that reads a
BEP-20 balance, a token's symbol, or a wallet's BNB balance still talks to the network
during a test. That makes those tests slow, rate-limited, dependent on mainnet state
that changes underneath them, and impossible to run in CI offline.

`ChainDouble` blocks outbound HTTP from handlers and answers JSON-RPC from state the
test sets up, so an agent that reads BNB Chain becomes as testable as one that doesn't:

    chain = ChainDouble()
    chain.add_token(FET, symbol="FET", decimals=18, balances={wallet: 5 * 10**18})

    with chain.install():
        result = await h.deliver(CheckBalance(wallet=wallet))

    assert result.reply(BalanceReport).amount == 5.0
    chain.assert_called("eth_call")

Anything the double has not been told about raises rather than silently returning zero,
so a test cannot pass on data that was never stubbed.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any, Iterator

#: BEP-20 / ERC-20 selectors. Stable across every token that implements the standard.
SELECTOR_BALANCE_OF = "70a08231"
SELECTOR_SYMBOL = "95d89b41"
SELECTOR_DECIMALS = "313ce567"
SELECTOR_TOTAL_SUPPLY = "18160ddd"
SELECTOR_NAME = "06fdde03"

#: FET on BNB Chain — the asset Agent Launch grants are paid in.
FET_BSC = "0x031b41e504677879370e9dbcf937283a8691fa7f"

BSC_CHAIN_ID = 56


class NetworkCallBlocked(AssertionError):
    """Raised when a handler tries to reach the network during a test."""


class UnstubbedCall(AssertionError):
    """Raised when a handler asks the chain for something the test never set up."""


def _norm(address: str) -> str:
    return address.lower()


def _enc_uint(value: int) -> str:
    return f"{value:064x}"


def _enc_string(value: str) -> str:
    raw = value.encode()
    padded = raw + b"\x00" * ((32 - len(raw) % 32) % 32)
    return _enc_uint(32) + _enc_uint(len(raw)) + padded.hex()


class _Token:
    def __init__(
        self,
        symbol: str,
        decimals: int,
        total_supply: int,
        balances: dict[str, int],
        name: str | None,
    ):
        self.symbol = symbol
        self.decimals = decimals
        self.total_supply = total_supply
        self.balances = {_norm(k): v for k, v in balances.items()}
        self.name = name if name is not None else symbol


class ChainDouble:
    """A stubbable JSON-RPC endpoint standing in for BNB Chain."""

    def __init__(self, *, chain_id: int = BSC_CHAIN_ID, block_number: int = 40_000_000):
        self.chain_id = chain_id
        self.block_number = block_number
        self._balances: dict[str, int] = {}
        self._tokens: dict[str, _Token] = {}
        self._code: dict[str, str] = {}
        #: Every JSON-RPC method the agent asked for, in order.
        self.calls: list[tuple[str, Any]] = []

    # -- setting up state ------------------------------------------------------

    def set_balance(self, address: str, wei: int) -> "ChainDouble":
        """Set a wallet's native BNB balance, in wei."""
        self._balances[_norm(address)] = wei
        return self

    def add_token(
        self,
        address: str,
        *,
        symbol: str,
        decimals: int = 18,
        total_supply: int = 0,
        balances: dict[str, int] | None = None,
        name: str | None = None,
    ) -> "ChainDouble":
        """Register a BEP-20 token and the balances it should report."""
        self._tokens[_norm(address)] = _Token(
            symbol, decimals, total_supply, balances or {}, name
        )
        self._code.setdefault(_norm(address), "0x60806040")
        return self

    def set_block_number(self, n: int) -> "ChainDouble":
        self.block_number = n
        return self

    # -- assertions ------------------------------------------------------------

    def assert_called(self, method: str) -> "ChainDouble":
        if not any(m == method for m, _ in self.calls):
            raise AssertionError(
                f"expected a {method} call; got {[m for m, _ in self.calls] or 'none'}"
            )
        return self

    def assert_no_calls(self) -> "ChainDouble":
        if self.calls:
            raise AssertionError(f"expected no chain access, got {self.calls}")
        return self

    # -- JSON-RPC --------------------------------------------------------------

    def rpc(self, payload: Any) -> Any:
        """Answer a single JSON-RPC request or a batch of them."""
        if isinstance(payload, list):
            return [self._one(p) for p in payload]
        return self._one(payload)

    def _one(self, payload: dict) -> dict:
        method = payload.get("method")
        params = payload.get("params") or []
        self.calls.append((method, params))
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id", 1),
            "result": self._dispatch(method, params),
        }

    def _dispatch(self, method: str, params: list) -> Any:
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(self.block_number)
        if method == "net_version":
            return str(self.chain_id)
        if method == "eth_getBalance":
            address = _norm(params[0])
            if address not in self._balances:
                raise UnstubbedCall(
                    f"handler read the BNB balance of {params[0]}, which the test never "
                    f"set. Call chain.set_balance({params[0]!r}, wei) first."
                )
            return hex(self._balances[address])
        if method == "eth_getCode":
            return self._code.get(_norm(params[0]), "0x")
        if method == "eth_getTransactionCount":
            return "0x0"
        if method == "eth_call":
            return self._eth_call(params[0])
        raise UnstubbedCall(
            f"handler called {method}, which ChainDouble does not implement. "
            f"Supported: eth_call, eth_getBalance, eth_getCode, eth_blockNumber, "
            f"eth_chainId, eth_getTransactionCount, net_version."
        )

    def _eth_call(self, call: dict) -> str:
        to = _norm(call.get("to", ""))
        data = (call.get("data") or call.get("input") or "").removeprefix("0x")
        selector, args = data[:8], data[8:]

        token = self._tokens.get(to)
        if token is None:
            raise UnstubbedCall(
                f"handler called contract {call.get('to')}, which the test never "
                f"registered. Call chain.add_token({call.get('to')!r}, symbol=...) first."
            )

        if selector == SELECTOR_BALANCE_OF:
            holder = "0x" + args[24:64]
            if holder not in token.balances:
                raise UnstubbedCall(
                    f"handler read the {token.symbol} balance of {holder}, which the "
                    f"test never set. Add it to the balances= of add_token()."
                )
            return "0x" + _enc_uint(token.balances[holder])
        if selector == SELECTOR_DECIMALS:
            return "0x" + _enc_uint(token.decimals)
        if selector == SELECTOR_TOTAL_SUPPLY:
            return "0x" + _enc_uint(token.total_supply)
        if selector == SELECTOR_SYMBOL:
            return "0x" + _enc_string(token.symbol)
        if selector == SELECTOR_NAME:
            return "0x" + _enc_string(token.name)
        raise UnstubbedCall(
            f"handler called selector 0x{selector} on {call.get('to')}; ChainDouble "
            f"implements balanceOf, symbol, name, decimals and totalSupply."
        )

    # -- installing over the HTTP clients --------------------------------------

    @contextmanager
    def install(self) -> Iterator["ChainDouble"]:
        """Route HTTP from handlers here, and block anything that isn't JSON-RPC.

        Patches whichever of requests / httpx / aiohttp are importable, so this works
        whether the agent uses web3.py (requests) or raw async HTTP.
        """
        with ExitStack() as stack:
            for patch in (self._patch_requests, self._patch_httpx, self._patch_aiohttp):
                ctx = patch()
                if ctx is not None:
                    stack.enter_context(ctx)
            yield self

    def _payload_or_block(self, url: str, json_body: Any, data: Any = None) -> Any:
        """Resolve a request body to a JSON-RPC payload, or refuse it.

        web3.py's HTTPProvider posts a JSON-encoded body via `data=` rather than
        `json=`, so both have to be understood.
        """
        payload = json_body
        if payload is None and data is not None:
            import json as _json

            if isinstance(data, (bytes, bytearray)):
                data = data.decode()
            if isinstance(data, str):
                try:
                    payload = _json.loads(data)
                except ValueError:
                    payload = None
        if payload is None:
            raise NetworkCallBlocked(
                f"handler made a non-JSON-RPC HTTP request to {url} during a test. "
                f"Stub it, or assert the handler does not call out."
            )
        return self.rpc(payload)

    def _patch_requests(self):
        try:
            import requests
        except ImportError:
            return None

        double = self

        class _Response:
            def __init__(self, payload):
                self._payload = payload
                self.status_code = 200
                self.reason = "OK"
                self.headers = {"Content-Type": "application/json"}

            def json(self):
                return self._payload

            @property
            def content(self):
                import json

                return json.dumps(self._payload).encode()

            @property
            def text(self):
                return self.content.decode()

            def raise_for_status(self):
                return None

        @contextmanager
        def _ctx():
            original = requests.Session.request

            def patched(self, method, url, *args, json=None, data=None, **kwargs):
                return _Response(double._payload_or_block(url, json, data))

            requests.Session.request = patched
            try:
                yield
            finally:
                requests.Session.request = original

        return _ctx()

    def _patch_httpx(self):
        try:
            import httpx
        except ImportError:
            return None

        double = self

        @contextmanager
        def _ctx():
            original = httpx.Client.request
            original_async = httpx.AsyncClient.request

            def _resp(url, json_body, data):
                return httpx.Response(
                    200, json=double._payload_or_block(str(url), json_body, data)
                )

            def patched(self, method, url, *args, json=None, content=None, **kwargs):
                return _resp(url, json, content)

            async def patched_async(
                self, method, url, *args, json=None, content=None, **kwargs
            ):
                return _resp(url, json, content)

            httpx.Client.request = patched
            httpx.AsyncClient.request = patched_async
            try:
                yield
            finally:
                httpx.Client.request = original
                httpx.AsyncClient.request = original_async

        return _ctx()

    def _patch_aiohttp(self):
        try:
            import aiohttp
        except ImportError:
            return None

        double = self

        class _Response:
            def __init__(self, payload):
                self._payload = payload
                self.status = 200

            async def json(self, **_kwargs):
                return self._payload

            async def text(self):
                import json

                return json.dumps(self._payload)

            def raise_for_status(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def release(self):
                return None

        @contextmanager
        def _ctx():
            original = aiohttp.ClientSession._request

            # _request is a coroutine function, and session.post() wraps its result
            # in _RequestContextManager, which awaits it. It must stay awaitable.
            async def patched(self, method, url, *args, json=None, data=None, **kwargs):
                return _Response(double._payload_or_block(str(url), json, data))

            aiohttp.ClientSession._request = patched
            try:
                yield
            finally:
                aiohttp.ClientSession._request = original

        return _ctx()
