"""In-memory storage that stands in for the on-disk KeyValueStore during tests."""

from typing import Any

from uagents.storage import StorageAPI


class InMemoryStore(StorageAPI):
    """A StorageAPI backed by a plain dict.

    The stock KeyValueStore writes a JSON file next to the test run and reloads it
    on the next one, so agent state leaks between tests and between CI jobs. This
    keeps the same interface but throws the state away when the harness does.
    """

    def __init__(self, initial: dict[str, Any] | None = None):
        self._data: dict[str, Any] = dict(initial or {})

    def get(self, key: str) -> Any | None:
        return self._data.get(key)

    def has(self, key: str) -> bool:
        return key in self._data

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def remove(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    # Mapping-ish sugar so tests can assert without reaching for .get()

    def __getitem__(self, key: str) -> Any:
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def as_dict(self) -> dict[str, Any]:
        """A copy of the full contents, for snapshot-style assertions."""
        return dict(self._data)

    def __repr__(self) -> str:
        return f"InMemoryStore({self._data!r})"
