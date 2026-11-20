"""python -m unittest discover -s tests  (from the repo root)

batch-2026-11-20.log: inmigraforma made 1 of 3.

- The PERM brief's facts said comments close December 17, 2026; the Federal
  Register page says January 19, 2027. The reader compared each draft with
  the brief and flagged the correct date for six rounds.
- The G-1055 brief carried a December 21 cut-off for old editions. The forms
  updates page says only that a new edition dated 11/19/26 exists.
- The hook grader, which is not told today's date, marked every hook down
  as a "false 2026 date".
"""
import os
import sys
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ACCOUNT", "inmigraforma")

import dates  # noqa: E402
import generate_batch as gb  # noqa: E402
import hooks  # noqa: E402
import llm  # noqa: E402
import newsroom  # noqa: E402
import search  # noqa: E402

PERM = ("https://www.federalregister.gov/documents/2026/11/17/2026-23370/"
        "modernizing-the-labor-market-test-and-improving-protections-for-us-"
        "workers-in-the-perm-immigrant")
PERM_PAGE = ("Federal Register :: Modernizing the Labor Market Test\nProposed Rule\n"
             "Published 11/17/2026\nComments Close: 01/19/2027\n"
             "DATES: Comments must be received on or before January 19, 2027.\n"
             + "The Department proposes to modernize the labor market test. " * 30)
G1055 = "https://www.uscis.gov/forms/forms-updates/g-1055-2026-11-19"
G1055_PAGE = ("Update to Form G-1055, Fee Schedule. New Edition Dated 11/19/26.\n"
              "November 19, 2026\nEdition Date: 11/19/26. You can find the edition "
              "date at the bottom of the page of Form G-1055, Fee Schedule.\n" * 3)


class FactDates(unittest.TestCase):
    def test_the_perm_deadline_guess_is_caught(self):
        self.assertEqual(dates.missing_from(
            "Comments close December 17, 2026.", PERM_PAGE), [(2026, 12, 17)])
        self.assertEqual(dates.missing_from(
            "Comentarios hasta el 17 de diciembre.", PERM_PAGE), [(None, 12, 17)])

    def test_the_real_deadline_passes_in_any_form(self):
        for f in ("Comments close January 19, 2027.",
                  "El público puede comentar antes del 19 de enero de 2027.",
                  "Hasta el 19 de enero.", "Deadline: 2027-01-19.",
                  "Published November 17, 2026."):
            self.assertEqual(dates.missing_from(f, PERM_PAGE), [], f)

    def test_a_two_digit_edition_date_counts(self):
        self.assertEqual(dates.missing_from(
            "The new edition is dated November 19, 2026.", G1055_PAGE), [])
        self.assertEqual(dates.missing_from(
            "Old editions are rejected from December 21, 2026.", G1055_PAGE),
            [(2026, 12, 21)])

    def test_words_that_only_look_like_months_are_not_dates(self):
        self.assertEqual(dates.mentioned(
            "Section 12 of the I-131 form may 5 apply; set 5 rules; el mar 3"), set())
        self.assertEqual(dates.mentioned("May 5, 2026 and 5 dic. 2026"),
                         {(2026, 5, 5), (2026, 12, 5)})


def brief(topic, url, facts):
    return {"topic": topic, "source_url": url, "facts": facts,
            "quote": "q" * 50, "published": "2026-11-17", "audience": "broad",
            "why_now": "now"}


class Nov20Briefs(unittest.TestCase):
    def run_research(self, candidates, pages):
        def tool_loop(system, ask, *a, **kw):
            for u in pages:
                search.RETRIEVED.add(u)
            return [{"role": "user", "content": ask}], 1

        def fetch(url, max_chars=None):
            search.PAGES[url] = pages.get(url, "")
            return pages.get(url, "")

        with mock.patch.object(newsroom, "items", return_value=[]), \
                mock.patch("ideas.digest", return_value=""), \
                mock.patch.object(llm, "tool_loop", tool_loop), \
                mock.patch.object(llm, "structured",
                                  return_value={"briefs": candidates}), \
                mock.patch.object(search, "supports", return_value=None), \
                mock.patch.object(search, "fetch", fetch), \
                mock.patch.object(gb, "_paperwork", return_value=False), \
                mock.patch("builtins.print"):
            return gb.research(3, [])

    def test_the_wrong_deadline_leaves_the_brief(self):
        got = self.run_research([brief(
            "DOL proposes changes to PERM labor certification", PERM,
            ["The Department of Labor proposed changes to PERM on November 17, 2026.",
             "The proposal changes how employers test the labor market.",
             "Comments close December 17, 2026."])], {PERM: PERM_PAGE})
        self.assertEqual(len(got), 1)
        self.assertNotIn("Comments close December 17, 2026.", got[0]["facts"])
        self.assertEqual(len(got[0]["facts"]), 2)

    def test_a_brief_left_with_one_fact_is_dropped(self):
        got = self.run_research([brief(
            "USCIS publishes a new edition of the G-1055 fee schedule", G1055,
            ["USCIS requires the new edition from December 21, 2026.",
             "Old editions are rejected after December 21, 2026.",
             "The new edition is dated November 19, 2026."])], {G1055: G1055_PAGE})
        self.assertEqual(got, [])


class GraderKnowsToday(unittest.TestCase):
    def test_todays_date_is_in_the_message(self):
        seen = {}

        def fake(system, msg, *a, **k):
            seen["msg"], seen["system"] = msg, system
            return {"gradings": [{"label": "A", "score": 5, "rank": 1,
                                  "reason": "ok"}]}

        acct = gb.ACCT
        with mock.patch.object(llm, "structured", side_effect=fake):
            try:
                hooks.grade(acct, "post104-x", [{"headline": "Un juez ordenó",
                                                 "sub": "El 28 de septiembre de 2026.",
                                                 "authored": True}])
            except Exception:
                pass   # only the prompt matters here
        self.assertIn(f"TODAY IS {date.today().isoformat()}", seen["msg"])
        self.assertNotIn(date.today().isoformat(), seen["system"])


if __name__ == "__main__":
    unittest.main()
