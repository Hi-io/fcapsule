"""SQLite lifecycle tests that also protect local Windows development behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fcapsule.store import FCAPSuleStore


class StoreLifecycleTests(unittest.TestCase):
    def test_context_managed_connection_is_closed_after_a_store_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "fcapsule.db")
            store.set_setting("example", "value")
            self.assertEqual(store.get_setting("example"), "value")
            # This is intentionally a filesystem operation rather than an implementation
            # detail assertion: Windows rejects it when a SQLite handle remains open.
            (Path(directory) / "fcapsule.db").unlink()


if __name__ == "__main__":
    unittest.main()
