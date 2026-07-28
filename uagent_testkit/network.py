"""Run several agents against each other in-process, with no sockets involved."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, TypeVar

from uagents import Agent, Model

from ._recording import Sent
from .harness import AgentHarness, Delivery, HandlerNotFound

ModelT = TypeVar("ModelT", bound=Model)


class ConversationTooLong(AssertionError):
    """Raised when agents keep replying past the configured round limit."""


@dataclass
class Exchange:
    """One message that actually reached an agent, and what that agent did."""

    sender: str
    recipient: str
    schema_digest: str
    body: str
    delivery: Delivery

    def matches(self, model: type[Model]) -> bool:
        return self.schema_digest == Model.build_schema_digest(model)

    def parse(self, model: type[ModelT]) -> ModelT:
        return model.model_validate_json(self.body)


class Transcript:
    """Everything that happened after one message was injected into the network."""

    def __init__(self, exchanges: list[Exchange], undelivered: list[Sent]):
        self.exchanges = exchanges
        #: Messages addressed to agents the network doesn't know about.
        self.undelivered = undelivered

    def __len__(self) -> int:
        return len(self.exchanges)

    def __iter__(self):
        return iter(self.exchanges)

    def of(self, model: type[ModelT]) -> list[ModelT]:
        """Every message of this type that reached a handler."""
        return [e.parse(model) for e in self.exchanges if e.matches(model)]

    def to(self, recipient: str) -> list[Exchange]:
        return [e for e in self.exchanges if e.recipient == recipient]

    def assert_delivered(
        self, model: type[Model], *, to: str | None = None
    ) -> "Transcript":
        candidates = self.exchanges if to is None else self.to(to)
        if not any(e.matches(model) for e in candidates):
            where = f" to {to}" if to else ""
            raise AssertionError(
                f"{model.__name__} was never delivered{where}. "
                f"Transcript: {self.summary()}"
            )
        return self

    def assert_all_delivered(self) -> "Transcript":
        if self.undelivered:
            raise AssertionError(
                f"messages went to unknown addresses: {self.undelivered}"
            )
        return self

    def summary(self) -> str:
        if not self.exchanges:
            return "(no messages)"
        return " -> ".join(
            f"{e.sender[:10]}…/{e.recipient[:10]}…" for e in self.exchanges
        )

    def __repr__(self) -> str:
        return f"<Transcript {len(self.exchanges)} exchange(s): {self.summary()}>"


class AgentNetwork:
    """A set of agents that can address each other by their real addresses.

    Delivery is synchronous and depth-first-by-round, so a conversation replays
    the same way every time — no sleeps, no flakes.
    """

    def __init__(self, *agents: Agent | AgentHarness, persist_storage: bool = False):
        self._harnesses: dict[str, AgentHarness] = {}
        for agent in agents:
            self.add(agent, persist_storage=persist_storage)

    def add(
        self, agent: Agent | AgentHarness, *, persist_storage: bool = False
    ) -> AgentHarness:
        h = (
            agent
            if isinstance(agent, AgentHarness)
            else AgentHarness(agent, persist_storage=persist_storage)
        )
        self._harnesses[h.address] = h
        return h

    def __contains__(self, address: object) -> bool:
        return address in self._harnesses

    def __iter__(self) -> Iterable[AgentHarness]:
        return iter(self._harnesses.values())

    def harness(self, agent: Agent | AgentHarness | str) -> AgentHarness:
        address = self._address_of(agent)
        if address not in self._harnesses:
            raise KeyError(f"{address} is not part of this network")
        return self._harnesses[address]

    @staticmethod
    def _address_of(agent: Agent | AgentHarness | str) -> str:
        if isinstance(agent, str):
            return agent
        return agent.address

    async def send(
        self,
        message: Model,
        *,
        to: Agent | AgentHarness | str,
        sender: Agent | AgentHarness | str | None = None,
        max_rounds: int = 20,
    ) -> Transcript:
        """Inject `message` and let the resulting conversation play out.

        Every reply addressed to another agent in the network is delivered in
        turn, until nobody has anything left to say.
        """
        recipient = self._address_of(to)
        from_address = (
            self._address_of(sender) if sender is not None else _EXTERNAL_SENDER
        )

        queue: deque[tuple[str, str, Model | str, str]] = deque()
        queue.append((from_address, recipient, message, Model.build_schema_digest(message)))

        exchanges: list[Exchange] = []
        undelivered: list[Sent] = []

        while queue:
            if len(exchanges) >= max_rounds:
                raise ConversationTooLong(
                    f"conversation exceeded {max_rounds} deliveries; "
                    f"suspected reply loop. So far: {Transcript(exchanges, []).summary()}"
                )

            src, dst, payload, digest = queue.popleft()
            harness = self._harnesses.get(dst)
            if harness is None:
                # Shouldn't happen for queued items, but keeps the loop total.
                continue

            model = payload if isinstance(payload, Model) else None
            if model is None:
                model_class = harness.agent._models.get(digest)
                if model_class is None:
                    undelivered.append(Sent(dst, digest, payload))  # type: ignore[arg-type]
                    continue
                model = model_class.model_validate_json(payload)  # type: ignore[arg-type]

            try:
                delivery = await harness.deliver(model, sender=src)
            except HandlerNotFound:
                undelivered.append(Sent(dst, digest, model.model_dump_json()))
                continue

            exchanges.append(
                Exchange(
                    sender=src,
                    recipient=dst,
                    schema_digest=digest,
                    body=model.model_dump_json(),
                    delivery=delivery,
                )
            )

            for s in delivery.sent:
                if s.destination in self._harnesses:
                    queue.append((dst, s.destination, s.body, s.schema_digest))
                else:
                    undelivered.append(s)

        return Transcript(exchanges, undelivered)


#: Address used when a message is injected from outside the network.
_EXTERNAL_SENDER = "agent1q0000000000000000000000000000000000000000000000000000000000"
