"""A two-agent quote flow, written the way the uAgents docs write agents.

Note the module-level `Agent(...)` calls — the standard idiom. A trader asks an
oracle for a price; the oracle answers from its cache or records a miss.
"""

from uagents import Agent, Context, Model


class QuoteRequest(Model):
    symbol: str


class Quote(Model):
    symbol: str
    price: float


class QuoteUnavailable(Model):
    symbol: str
    reason: str


PRICES = {"FET": 1.42, "BNB": 604.10}


oracle = Agent(name="oracle", seed="uagent-testkit example oracle seed")
trader = Agent(name="trader", seed="uagent-testkit example trader seed")


@oracle.on_message(model=QuoteRequest, replies={Quote, QuoteUnavailable})
async def on_quote_request(ctx: Context, sender: str, msg: QuoteRequest):
    symbol = msg.symbol.upper()
    served = ctx.storage.get("served") or 0
    ctx.storage.set("served", served + 1)

    price = PRICES.get(symbol)
    if price is None:
        misses = ctx.storage.get("misses") or []
        ctx.storage.set("misses", [*misses, symbol])
        await ctx.send(
            sender, QuoteUnavailable(symbol=symbol, reason="symbol not tracked")
        )
        return

    await ctx.send(sender, Quote(symbol=symbol, price=price))


@trader.on_message(model=Quote)
async def on_quote(ctx: Context, sender: str, msg: Quote):
    ctx.storage.set("last_price", msg.price)


@trader.on_message(model=QuoteUnavailable)
async def on_quote_unavailable(ctx: Context, sender: str, msg: QuoteUnavailable):
    ctx.storage.set("last_error", msg.reason)


@trader.on_interval(period=60.0, messages=QuoteRequest)
async def poll_price(ctx: Context):
    """Would fire once a minute in production; the harness runs it on demand."""
    await ctx.send(oracle.address, QuoteRequest(symbol="FET"))
