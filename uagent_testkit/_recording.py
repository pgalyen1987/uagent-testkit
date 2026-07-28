"""Context subclasses that capture outbound messages instead of dispatching them."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from uagents import Model
from uagents.context import ExternalContext, InternalContext
from uagents_core.types import DeliveryStatus, MsgStatus

if TYPE_CHECKING:
    from uagents.types import JsonStr

ModelT = TypeVar("ModelT", bound=Model)


@dataclass
class Sent:
    """One message a handler passed to ctx.send()."""

    destination: str
    schema_digest: str
    body: "JsonStr"

    def matches(self, model: type[Model]) -> bool:
        return self.schema_digest == Model.build_schema_digest(model)

    def parse(self, model: type[ModelT]) -> ModelT:
        """Decode the payload as `model`.

        Raises if the wire schema digest doesn't match, so a renamed or retyped
        field is caught here rather than surfacing as a confusing attribute error.
        """
        if not self.matches(model):
            raise AssertionError(
                f"message sent to {self.destination} is not a {model.__name__} "
                f"(schema digest {self.schema_digest})"
            )
        return model.model_validate_json(self.body)

    def __repr__(self) -> str:
        return f"Sent(to={self.destination!r}, body={self.body})"


class _RecordingMixin:
    """Overrides send_raw so nothing touches the resolver, dispenser or network."""

    def _init_recording(self) -> None:
        self.sent: list[Sent] = []

    async def send_raw(  # type: ignore[override]
        self,
        destination: str,
        message_schema_digest: str,
        message_body: "JsonStr",
        sync: bool = False,
        wait_for_response: bool = False,
        timeout: int = 5,
        protocol_digest: str | None = None,
        queries: dict[str, Any] | None = None,
        expected_response_digests: set[str] | None = None,
    ) -> MsgStatus:
        self.sent.append(
            Sent(
                destination=destination,
                schema_digest=message_schema_digest,
                body=message_body,
            )
        )
        # Mirror the bookkeeping the real context does, so that the framework's own
        # validate_replies() still sees the reply and behaves exactly as in prod.
        self._outbound_messages.setdefault(destination, []).append(
            (message_body, message_schema_digest)
        )
        return MsgStatus(
            status=DeliveryStatus.DELIVERED,
            detail="captured by uagent-testkit",
            destination=destination,
            endpoint="",
            session=self._session,
        )


class RecordingExternalContext(_RecordingMixin, ExternalContext):
    """The context handed to on_message handlers under test."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_recording()


class RecordingInternalContext(_RecordingMixin, InternalContext):
    """The context handed to on_interval and on_event handlers under test."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_recording()


@dataclass
class LogCapture:
    """Collects records emitted through the agent's logger during one run."""

    records: list[logging.LogRecord] = field(default_factory=list)

    def messages(self, level: int | None = None) -> list[str]:
        return [
            r.getMessage()
            for r in self.records
            if level is None or r.levelno >= level
        ]

    @property
    def warnings(self) -> list[str]:
        return self.messages(logging.WARNING)

    @property
    def errors(self) -> list[str]:
        return self.messages(logging.ERROR)


class _ListHandler(logging.Handler):
    def __init__(self, capture: LogCapture):
        super().__init__()
        self._capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        self._capture.records.append(record)
