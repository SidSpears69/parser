"""Persistent error file rotation."""

from pathlib import Path
import tempfile
import unittest

from web_parser.error_file import ErrorFile


class ErrorFileTests(unittest.TestCase):
    def test_rotation_and_recent_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = ErrorFile("sail-master.ru", Path(folder), max_bytes=12, backups=2)
            for number in range(4):
                journal.append(f"line{number}")
            self.assertEqual(journal.recent(), ("line2", "line3"))
            self.assertEqual(
                journal.path.with_name(journal.path.name + ".1").read_text(encoding="utf-8"),
                "line0\nline1\n",
            )
            journal.append("line4")
            self.assertEqual(journal.recent(), ("line4",))
            self.assertEqual(
                journal.path.with_name(journal.path.name + ".1").read_text(encoding="utf-8"),
                "line2\nline3\n",
            )
