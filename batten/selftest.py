"""`batten selftest` — runs the full offline suite in batten/tests/.

Equivalent to: python3 -m unittest batten.tests.test_geo ... (all modules)
or:            python3 -m unittest discover -t . -s batten/tests -v
Nothing here touches the network.
"""

from __future__ import annotations

import sys
import unittest

TEST_MODULES = [
    "test_brain",
    "test_cli",
    "test_config",
    "test_geo",
    "test_noaa",
    "test_notify",
    "test_providers",
    "test_replay",
    "test_state",
    "test_supplies",
    "test_tui",
    "test_units",
    "test_web",
    "test_watcher",
    "test_worstcase",
]


def run() -> bool:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for mod in TEST_MODULES:
        suite.addTests(loader.loadTestsFromName(f"batten.tests.{mod}"))
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
