"""
Mail accounts from .env — no network.

    .venv\\Scripts\\python.exe -m unittest tests.test_mailboxes -v
"""
import isolate  # noqa: F401  (must come first)
import os
import unittest
from unittest import mock

import mailboxes

TWO = {"MAIL_IMAP_HOST": "imap.gmx.net", "MAIL_USER": "a@gmx.de", "MAIL_PASSWORD": "x",
       "MAIL2_IMAP_HOST": "imap.gmail.com", "MAIL2_USER": "b@gmail.com", "MAIL2_PASSWORD": "y",
       "MAIL2_NAME": "Gmail", "MAIL3_IMAP_HOST": "imap.web.de", "MAIL3_USER": "c@web.de"}   # MAIL3 has no password


class Mailboxes(unittest.TestCase):
    def test_first_account_keeps_the_old_names_and_more_follow(self):
        with mock.patch.dict(os.environ, TWO, clear=False):
            accs = mailboxes.accounts()
        self.assertEqual([(a["key"], a["name"], a["host"]) for a in accs],
                         [("1", "a@gmx.de", "imap.gmx.net"), ("2", "Gmail", "imap.gmail.com")],
                         "an incomplete account (no password) is left out")
        self.assertEqual(accs[0]["port"], 993)

    def test_find_by_name_or_address(self):
        with mock.patch.dict(os.environ, TWO, clear=False):
            self.assertEqual(mailboxes.find("gmail")["user"], "b@gmail.com")
            self.assertEqual(mailboxes.find("a@gmx")["user"], "a@gmx.de")
            self.assertEqual(mailboxes.find("")["user"], "a@gmx.de", "no name: the first one")

    def test_one_broken_account_does_not_stop_the_others(self):
        boxes = {"imap.gmx.net": mock.MagicMock()}

        def fake(host, port, timeout=30):
            if host not in boxes:
                raise OSError("connection refused")
            return boxes[host]
        with mock.patch.dict(os.environ, TWO, clear=False), mock.patch("imaplib.IMAP4_SSL", side_effect=fake):
            results, errors = mailboxes.each(lambda acc, box: acc["name"])
        self.assertEqual(results, ["a@gmx.de"])
        self.assertTrue(errors and errors[0].startswith("Gmail:"))
        boxes["imap.gmx.net"].select.assert_called_with("INBOX", readonly=True)


if __name__ == "__main__":
    unittest.main()
