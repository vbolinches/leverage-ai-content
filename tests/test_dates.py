"""python -m unittest discover -s tests  (from the repo root)"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dates import parse_published  # noqa: E402


class ParsePublished(unittest.TestCase):
    def check(self, raw, want):
        self.assertEqual(parse_published(raw), want, raw)

    def test_every_date_dropped_on_2026_11_14(self):
        # Copied from batch-2026-11-14.log, "unreadable date ..." lines.
        for raw, want in [("Oct 20, 2026", date(2026, 10, 20)),
                          ("Oct 9, 2026", date(2026, 10, 9)),
                          ("Sep 30, 2026", date(2026, 9, 30)),
                          ("Oct 26, 2026", date(2026, 10, 26)),
                          ("Oct 14, 2026", date(2026, 10, 14))]:
            self.check(raw, want)

    def test_iso(self):
        self.check("2026-10-20", date(2026, 10, 20))
        self.check("2026-10-20T14:03:00Z", date(2026, 10, 20))
        self.check("2026-10-20 14:03", date(2026, 10, 20))
        self.check("2026-09", date(2026, 9, 1))
        self.check("2026/10/20", date(2026, 10, 20))

    def test_us_month_day_year(self):
        self.check("October 20, 2026", date(2026, 10, 20))
        self.check("Oct. 20, 2026", date(2026, 10, 20))
        self.check("Sept. 9, 2026", date(2026, 9, 9))
        self.check("Oct 20 2026", date(2026, 10, 20))
        self.check("October 20th, 2026", date(2026, 10, 20))
        self.check("Monday, October 19, 2026", date(2026, 10, 19))
        self.check("Mar 5, 2026", date(2026, 3, 5))
        self.check("May 1, 2026", date(2026, 5, 1))

    def test_day_month_year(self):
        self.check("20 Oct 2026", date(2026, 10, 20))
        self.check("20 October, 2026", date(2026, 10, 20))
        self.check("Tue, 20 Oct 2026 14:00:00 GMT", date(2026, 10, 20))

    def test_spanish(self):
        self.check("20 de octubre de 2026", date(2026, 10, 20))
        self.check("1 de enero de 2027", date(2027, 1, 1))
        self.check("9 de septiembre de 2026", date(2026, 9, 9))
        self.check("9 de setiembre de 2026", date(2026, 9, 9))
        self.check("20 oct. 2026", date(2026, 10, 20))
        self.check("15 de abril del 2026", date(2026, 4, 15))
        self.check("31 de diciembre de 2026", date(2026, 12, 31))
        self.check("lunes, 19 de octubre de 2026", date(2026, 10, 19))
        self.check("miércoles, 14 de octubre de 2026", date(2026, 10, 14))
        self.check("Fecha: 20 de octubre de 2026", date(2026, 10, 20))

    def test_month_only(self):
        # The Visa Bulletin is dated to the month.
        self.check("September 2026", date(2026, 9, 1))
        self.check("March 2026", date(2026, 3, 1))
        self.check("septiembre de 2026", date(2026, 9, 1))
        self.check("marzo de 2026", date(2026, 3, 1))

    def test_numeric_only_when_unambiguous(self):
        self.check("10/20/2026", date(2026, 10, 20))   # can only be month-first
        self.check("20/10/2026", date(2026, 10, 20))   # can only be day-first
        self.check("05/05/2026", date(2026, 5, 5))     # same either way
        self.check("05/06/2026", None)                 # May 6 or June 5

    def test_unreadable(self):
        for raw in (None, "", "   ", "2026", "recently", "Octember 20, 2026",
                    "Feb 30, 2026", "2026-13-01", "N/A", 20261020):
            self.check(raw, None)


class NotNews(unittest.TestCase):
    """_not_news() is the gate that dropped the six briefs."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ACCOUNT", "inmigraforma")
        try:
            import generate_batch
        except (Exception, SystemExit) as e:  # account config / local model not available
            raise unittest.SkipTest(f"generate_batch not importable here: {e}")
        cls.gb = generate_batch

    def test_us_date_is_news_again(self):
        today = date.today()
        raw = today.strftime("%b ") + str(today.day) + today.strftime(", %Y")
        self.assertIsNone(self.gb._not_news(raw))

    def test_garbage_still_dropped(self):
        self.assertIn("unreadable", self.gb._not_news("recently"))

    def test_missing_still_dropped(self):
        self.assertIn("no date", self.gb._not_news(""))


if __name__ == "__main__":
    unittest.main()
