"""CLI: parser wiring, supplies commands, init preference updates."""

import io
import json
import unittest
from contextlib import redirect_stdout

from . import temp_home, write_config
from .. import cli, config


class TestParser(unittest.TestCase):
    def test_all_subcommands_wired(self):
        p = cli.build_parser()
        for name in ("init", "status", "brief", "chat", "watch", "worstcase",
                     "cone", "supplies", "doctor", "selftest", "demo", "tui", "serve"):
            args = p.parse_args([name])
            self.assertTrue(hasattr(args, "func"), name)

    def test_init_flags_parse(self):
        p = cli.build_parser()
        args = p.parse_args(["init", "--lat", "1", "--lon", "2", "--units", "metric",
                             "--web-port", "9000", "--household", "4"])
        self.assertEqual(args.units, "metric")
        self.assertEqual(args.web_port, 9000)

    def test_tui_headless_flags(self):
        p = cli.build_parser()
        args = p.parse_args(["tui", "--headless", "/tmp/x", "--source", "demo",
                             "--units", "metric"])
        self.assertEqual(args.headless, "/tmp/x")
        self.assertEqual(args.source, "demo")


class TestSuppliesCommand(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_supplies(cli.build_parser().parse_args(["supplies"] + argv))
        return buf.getvalue()

    def test_list_and_mark(self):
        with temp_home():
            write_config()
            self._run(["have", "water", "pantry"])
            out = self._run([])
            self.assertIn("✅", out)
            self.assertIn("pantry", out)
            self.assertIn("1 ready", out)

    def test_na_and_need(self):
        with temp_home():
            write_config()
            self._run(["na", "pets"])
            self._run(["have", "water"])
            out = self._run(["need", "water"])
            self.assertIn("❌", out)   # water back to missing

    def test_unknown_item_exits(self):
        with temp_home():
            write_config()
            with self.assertRaises(SystemExit):
                self._run(["have", "unicorn"])

    def test_list_ids(self):
        with temp_home():
            write_config()
            out = self._run(["--list-ids"])
            self.assertIn("water", out)
            self.assertIn("gobag", out)
        self.assertIn("water", out)
        self.assertIn("gobag", out)


class TestInitCommand(unittest.TestCase):
    def test_units_update_without_coords(self):
        with temp_home():
            write_config(lat=25.0, lon=-80.0)
            cli.cmd_init(cli.build_parser().parse_args(
                ["init", "--units", "metric", "--place", "Renamed"]))
            cfg = config.load_config()
            self.assertEqual(cfg["units"], "metric")
            self.assertEqual(cfg["place"], "Renamed")
            self.assertEqual(cfg["lat"], 25.0)      # preserved

    def test_init_requires_coords_when_blank(self):
        with temp_home():
            with self.assertRaises(SystemExit):
                cli.cmd_init(cli.build_parser().parse_args(["init"]))


if __name__ == "__main__":
    unittest.main()
