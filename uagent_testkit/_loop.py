"""Keep an event loop available for Agent construction.

`Agent.__init__` calls `get_event_loop()` and stores the result, so building an
agent at import time or inside a synchronous pytest fixture raises
`RuntimeError: There is no current event loop` on Python 3.12+. Since the canonical
uAgents idiom is a module-level `agent = Agent(...)`, that would make most real
agents untestable without restructuring them. We make sure a loop is set instead.
"""

from __future__ import annotations

import asyncio


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return the current event loop, creating and installing one if needed."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        loop = None

    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop
