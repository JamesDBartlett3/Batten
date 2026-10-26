"""Notification plumbing: Discord chunking (retries not tested — no network)."""

import unittest

from ..notify import _chunk


class TestChunking(unittest.TestCase):
    def test_long_text_splits_under_limit(self):
        text = "\n".join(f"line {i} " + "x" * 40 for i in range(120))
        chunks = _chunk(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 1900)

    def test_no_content_lost(self):
        text = "\n".join(f"L{i}" for i in range(300))
        joined = "".join(_chunk(text)).replace("\n", "")
        self.assertEqual(joined, text.replace("\n", ""))

    def test_short_text_single_chunk(self):
        self.assertEqual(_chunk("hello"), ["hello"])

    def test_prefers_line_boundaries(self):
        text = ("a" * 1800) + "\n" + ("b" * 100)
        chunks = _chunk(text)
        self.assertEqual(chunks[0], "a" * 1800)     # split on the newline


if __name__ == "__main__":
    unittest.main()
