"""Drive a uAgent's handlers directly, with no network and no Almanac registration."""

from __future__ import annotations

import logging
import uuid
from typing import Any, TypeVar

from uagents import Agent, Model
from uagents.agent import AgentRepresentation
from uagents.types import MsgInfo
from uagents_core.identity import Identity, generate_user_address, is_user_address

from ._recording import (
    LogCapture,
    RecordingExternalContext,
    RecordingInternalContext,
    Sent,
    _ListHandler,
)
from ._storage import InMemoryStore

ModelT = TypeVar("ModelT", bound=Model)

#: Stand-in peer used when a test doesn't care who sent the message.
DEFAULT_SENDER = Identity.from_seed("uagent-testkit default sender", 0).address


class _Any:
    """Sentinel meaning 'any destination'. See Delivery.replies."""

    def __repr__(self) -> str:
        return "ANY"


#: Pass as `to=` to match messages sent to any destination.
ANY = _Any()


class HandlerNotFound(AssertionError):
    """Raised when the agent has no handler registered for a message type."""


class UnverifiedSender(AssertionError):
    """Raised when a user address targets a handler that requires a verified agent."""


class Delivery:
    """What happened when one message was handed to the agent.

    Wraps the messages the handler sent, anything it logged, and any exception it
    raised. The framework itself catches handler exceptions and writes them to a
    log, which makes a broken handler look like a passing test; the harness keeps
    the exception so it can be re-raised or asserted on.
    """

    def __init__(
        self,
        sent: list[Sent],
        logs: LogCapture,
        error: BaseException | None,
        received: Model,
        received_digest: str,
        sender: str,
        session: uuid.UUID,
        replies: dict[str, dict[str, type[Model]]] | None,
    ):
        self.sent = sent
        self.logs = logs
        self.error = error
        self.received = received
        self.received_digest = received_digest
        self.sender = sender
        self.session = session
        self._replies = replies or {}

    # -- querying what was sent ------------------------------------------------

    def to(self, destination: str) -> list[Sent]:
        """Every message sent to one address."""
        return [s for s in self.sent if s.destination == destination]

    def replies(
        self, model: type[ModelT], *, to: str | None = None
    ) -> list[ModelT]:
        """Messages of type `model` sent back to the original sender.

        Pass `to` for messages sent to a third party instead, or `to=ANY` for
        every message of that type regardless of destination.
        """
        target = self.sender if to is None else to
        return [
            s.parse(model)
            for s in self.sent
            if s.matches(model) and (target is ANY or s.destination == target)
        ]

    def reply(self, model: type[ModelT], *, to: str | None = None) -> ModelT:
        """The single message of type `model`. Raises unless there is exactly one."""
        found = self.replies(model, to=to)
        target = self.sender if to is None else to
        if not found:
            raise AssertionError(
                f"expected one {model.__name__} to {target}, got none. "
                f"Sent: {self.sent or '(nothing)'}"
            )
        if len(found) > 1:
            raise AssertionError(
                f"expected one {model.__name__} to {target}, got {len(found)}"
            )
        return found[0]

    # -- assertions ------------------------------------------------------------

    def assert_replied_with(
        self, model: type[Model], *, to: str | None = None
    ) -> "Delivery":
        self.reply(model, to=to)
        return self

    def assert_silent(self) -> "Delivery":
        if self.sent:
            raise AssertionError(f"expected no messages, got {self.sent}")
        return self

    def assert_no_errors(self) -> "Delivery":
        if self.error is not None:
            raise AssertionError(f"handler raised {self.error!r}") from self.error
        if self.logs.errors:
            raise AssertionError(f"handler logged errors: {self.logs.errors}")
        return self

    def assert_reply_contract(self) -> "Delivery":
        """Assert the handler honoured the `replies=` set on its @on_message.

        The framework only logs when a handler silently fails to answer, which is
        easy to miss. Under test it should be a failure.
        """
        allowed = self._replies.get(self.received_digest)
        if not allowed:
            return self
        sent_digests = {s.schema_digest for s in self.to(self.sender)}
        if not sent_digests & set(allowed):
            expected = sorted(m.__name__ for m in allowed.values())
            raise AssertionError(
                f"handler for {type(self.received).__name__} declares replies "
                f"{expected} but sent none of them to {self.sender}"
            )
        return self

    def raise_for_error(self) -> "Delivery":
        if self.error is not None:
            raise self.error
        return self

    def __repr__(self) -> str:
        return (
            f"<Delivery {type(self.received).__name__} from {self.sender[:16]}… "
            f"sent={len(self.sent)} error={self.error!r}>"
        )


class AgentHarness:
    """A uAgent wired for tests.

    Swaps the agent's on-disk storage for an in-memory one, merges the internal
    protocol so handlers are resolvable, and routes messages straight to the
    registered handler with a context that records instead of transmitting.
    """

    def __init__(self, agent: Agent, *, persist_storage: bool = False):
        self.agent = agent
        if not persist_storage:
            agent._storage = InMemoryStore()
        self._ensure_protocol_included(agent)

    @staticmethod
    def _ensure_protocol_included(agent: Agent) -> None:
        """Merge the agent's internal protocol into its handler tables.

        Normally Agent.setup() does this, but setup() also starts the Almanac
        registration loop and the network dispenser. include() on its own does
        the routing half with no I/O.
        """
        if getattr(agent, "_testkit_included", False):
            return
        agent.include(agent._protocol)
        agent._testkit_included = True  # type: ignore[attr-defined]

    # -- properties ------------------------------------------------------------

    @property
    def address(self) -> str:
        return self.agent.address

    @property
    def name(self) -> str:
        return self.agent.name

    @property
    def storage(self) -> InMemoryStore:
        return self.agent._storage  # type: ignore[return-value]

    def handles(self, model: type[Model]) -> bool:
        """Whether a handler is registered for this message type."""
        return Model.build_schema_digest(model) in self.agent._models

    # -- driving handlers ------------------------------------------------------

    async def deliver(
        self,
        message: Model,
        *,
        sender: str = DEFAULT_SENDER,
        session: uuid.UUID | None = None,
        raise_errors: bool = True,
    ) -> Delivery:
        """Hand `message` to the agent as though `sender` had transmitted it."""
        digest = Model.build_schema_digest(message)
        model_class = self.agent._models.get(digest)
        if model_class is None:
            raise HandlerNotFound(
                f"{type(message).__name__} has no registered handler on "
                f"agent {self.agent.name!r}"
            )

        # Mirror the framework's own resolution order, including its refusal to
        # hand a user-address message to a handler that requires a verified agent.
        handler = self.agent._unsigned_message_handlers.get(digest)
        if handler is None:
            if is_user_address(sender):
                if digest in self.agent._signed_message_handlers:
                    raise UnverifiedSender(
                        f"{type(message).__name__} is handled by a verified-only "
                        f"handler, but {sender!r} is a user address. In production "
                        f"this message is rejected with an ErrorMessage. Use an "
                        f"agent address, or set allow_unverified=True on the handler."
                    )
            else:
                handler = self.agent._signed_message_handlers.get(digest)
        if handler is None:
            raise HandlerNotFound(
                f"no handler function for {type(message).__name__} on "
                f"agent {self.agent.name!r}"
            )

        session = session or uuid.uuid4()
        body = message.model_dump_json()
        ctx = self._external_context(digest, body, sender, session)

        with self._capture_logs() as logs:
            error: BaseException | None = None
            try:
                await handler(ctx, sender, model_class.model_validate_json(body))
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
                error = exc

        delivery = Delivery(
            sent=ctx.sent,
            logs=logs,
            error=error,
            received=message,
            received_digest=digest,
            sender=sender,
            session=session,
            replies=self.agent._replies,
        )
        if raise_errors and error is not None:
            raise error
        return delivery

    async def tick(self, *, only: str | None = None) -> Delivery:
        """Run the agent's @on_interval handlers once.

        Periods are ignored — the point is to exercise the body deterministically
        rather than wait out a wall-clock schedule. `only` selects one handler by
        function name.
        """
        handlers = [
            (fn, period)
            for fn, period in self.agent._interval_handlers
            if only is None or fn.__name__ == only
        ]
        if only is not None and not handlers:
            raise HandlerNotFound(f"no interval handler named {only!r}")
        return await self._run_context_handlers(
            [fn for fn, _ in handlers], label="interval"
        )

    async def startup(self) -> Delivery:
        """Run the agent's @on_event("startup") handlers."""
        return await self._run_context_handlers(self.agent._on_startup, label="startup")

    async def shutdown(self) -> Delivery:
        """Run the agent's @on_event("shutdown") handlers."""
        return await self._run_context_handlers(
            self.agent._on_shutdown, label="shutdown"
        )

    # -- internals -------------------------------------------------------------

    async def _run_context_handlers(self, handlers, *, label: str) -> Delivery:
        ctx = self._internal_context(uuid.uuid4())
        error: BaseException | None = None
        with self._capture_logs() as logs:
            for fn in handlers:
                try:
                    await fn(ctx)
                except BaseException as exc:  # noqa: BLE001
                    error = exc
                    break
        delivery = Delivery(
            sent=ctx.sent,
            logs=logs,
            error=error,
            received=_Trigger(label=label),
            received_digest="",
            sender=self.address,
            session=ctx.session,
            replies=None,
        )
        if error is not None:
            raise error
        return delivery

    def _representation(self) -> AgentRepresentation:
        agent = self.agent
        return AgentRepresentation(
            address=agent.address,
            name=agent._name,
            identity=agent._identity,
            prefix=agent._prefix,
        )

    def _common_context_kwargs(self, session: uuid.UUID) -> dict[str, Any]:
        agent = self.agent
        return {
            "agent": self._representation(),
            "storage": agent._storage,
            "ledger": agent._ledger,
            "resolver": agent._resolver,
            "dispenser": agent._dispenser,
            "logger": agent._logger,
            "session": session,
            "message_history": agent._message_history,
        }

    def _external_context(
        self, digest: str, body: str, sender: str, session: uuid.UUID
    ) -> RecordingExternalContext:
        return RecordingExternalContext(
            queries={},
            replies=self.agent._replies,
            message_received=MsgInfo(message=body, sender=sender, schema_digest=digest),
            protocol=self.agent.get_message_protocol(digest),
            **self._common_context_kwargs(session),
        )

    def _internal_context(self, session: uuid.UUID) -> RecordingInternalContext:
        return RecordingInternalContext(
            interval_messages=self.agent._interval_messages,
            **self._common_context_kwargs(session),
        )

    def _capture_logs(self):
        return _LogCaptureScope(self.agent._logger)


class _Trigger(Model):
    """Placeholder 'received message' for interval/startup runs."""

    label: str


class _LogCaptureScope:
    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._capture = LogCapture()
        self._handler = _ListHandler(self._capture)

    def __enter__(self) -> LogCapture:
        self._logger.addHandler(self._handler)
        return self._capture

    def __exit__(self, *exc_info) -> None:
        self._logger.removeHandler(self._handler)


def harness(agent: Agent, *, persist_storage: bool = False) -> AgentHarness:
    """Wrap an agent for testing. See AgentHarness."""
    return AgentHarness(agent, persist_storage=persist_storage)


def user_sender() -> str:
    """A user (non-agent) address, for exercising allow_unverified handlers."""
    return generate_user_address()
