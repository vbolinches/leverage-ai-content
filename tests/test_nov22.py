"""python -m unittest discover -s tests  (from the repo root)

batch-2026-11-22.log, the first run with every fix: inmigraforma made 1 of 3.

- The PERM proposal was cited as its govinfo PDF; fetch() reads text, not
  PDF, so the brief died as "the page could not be opened". GPO prints the
  same document as HTML at /html/<number>.htm, which is read in its place.
- A second brief died the same way with no reason given (404? 403?).
- Every Visa Bulletin fact was dropped as "a date not on its page": the
  bulletin writes its cut-offs as 01APR25, which no date pattern read.
- The USCIS fee brief cited uscis.gov/forms/forms-updates, a list of many
  updates, and only 1 of its 3 facts was on it, so it was never written.
- The PERM post's first clarity fix broke a cover rule and was dropped
  silently; the second thought for 16000 tokens and hit its cap, and the post
  was rejected with two doubts left.
- Nov 21's log helper was named _clip and replaced the research _clip that
  trims signals and office text to a size, so those lost their line breaks.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ACCOUNT", "inmigraforma")

import dates  # noqa: E402
import generate_batch as gb  # noqa: E402
import search  # noqa: E402

PDF = ("https://www.govinfo.gov/content/pkg/FR-2026-11-17/pdf/2026-23370.pdf"
       "?pubDate=20261119")
HTM = "https://www.govinfo.gov/content/pkg/FR-2026-11-17/html/2026-23370.htm"


class GovinfoPdfIsReadAsHtml(unittest.TestCase):
    def setUp(self):
        search.PAGES.clear()
        search.RETRIEVED.clear()

    def test_twin(self):
        self.assertEqual(search.html_twin(PDF), HTM)
        self.assertIsNone(search.html_twin("https://www.uscis.gov/x.pdf"))

    def test_pdf_citation_is_checked_against_the_html(self):
        seen = []

        def get(url):
            seen.append(url)
            if url == HTM:
                return (b"<p>The Department proposes to modernize the labor "
                        b"market test in the PERM program and to improve "
                        b"protections for U.S. workers.</p>", "text/html", url)
            raise OSError("HTTP Error 415")

        with mock.patch.object(search, "_get_urllib", get), \
                mock.patch.object(search, "covered_by", lambda u: None):
            why = search.supports(PDF, "The Department proposes to modernize "
                                       "the labor market test in the PERM program")
        self.assertIsNone(why)
        self.assertEqual(seen, [HTM])
        self.assertIn(PDF, search.RETRIEVED)


class UnopenedPageSaysWhy(unittest.TestCase):
    def test_reason_is_named(self):
        url = "https://www.uscis.gov/fees/fee-schedule"
        search.PAGES.pop(url, None)

        def bad(u):
            raise OSError("HTTP Error 404: Not Found")

        def curl(u):
            raise OSError("curl: (22) The requested URL returned error: 404")

        with mock.patch.object(search, "_get_urllib", bad), \
                mock.patch.object(search, "_get_curl", curl):
            why = search.supports(url, "x" * 60)
        self.assertIn("could not be opened (", why)
        self.assertIn("404", why)
        self.assertTrue(why.endswith(url))


class VisaBulletinDates(unittest.TestCase):
    PAGE = ("EB-2 | 01APR25 | 01APR25 | 01NOV13 | 01APR25\n"
            "F2A | 01FEB24 | 15DEC23")

    def test_cutoffs_are_read(self):
        got = dates.mentioned(self.PAGE)
        self.assertIn((2025, 4, 1), got)
        self.assertIn((2013, 11, 1), got)
        self.assertIn((2023, 12, 15), got)

    def test_fact_survives(self):
        fact = ("EB-2 for most countries advanced to April 1, 2025; EB-2 "
                "India held at November 1, 2013.")
        self.assertEqual(dates.missing_from(fact, self.PAGE), [])

    def test_wrong_date_still_dropped(self):
        self.assertEqual(dates.missing_from("advanced to May 1, 2025", self.PAGE),
                         [(2025, 5, 1)])

    def test_not_inside_words(self):
        self.assertEqual(dates.mentioned("form AB01APR25X and 101APR257"), set())


class FormsUpdatesIsAListing(unittest.TestCase):
    def test_listing(self):
        self.assertTrue(gb._listing("https://www.uscis.gov/forms/forms-updates"))
        self.assertFalse(gb._listing("https://www.uscis.gov/i-131"))


class ResearchClipKeepsLines(unittest.TestCase):
    def test_research_clip_is_not_the_log_clip(self):
        self.assertEqual(gb._clip("a\nb", 100), "a\nb")
        self.assertEqual(gb._clip_line("a\n  b"), "a b")


BROKE = "slide 1: the sub says 'gobierno', a word the first sentence never uses"


def _post(tag):
    return {"tag": tag, "slides": [{"kind": "cover", "headline": "h", "sub": "s"}],
            "caption": "c"}


class ClarityFixRetries(unittest.TestCase):
    def _run(self, answers):
        calls = []

        def structured(*a, **k):
            calls.append(k)
            ans = answers.pop(0)
            if isinstance(ans, Exception):
                raise ans
            return ans

        def validate(p):
            return [BROKE] if p.get("tag") == "bad" else []

        def read(p, *a, **k):
            return ([] if p.get("tag") == "good"
                    else ["A first-time reader stopped at 'h': what is PERM?"]), {}

        with mock.patch.object(gb.llm, "structured", structured), \
                mock.patch.object(gb, "validate", validate), \
                mock.patch.object(gb.clarity, "read", read), \
                mock.patch.object(gb, "_snap", lambda *a, **k: None), \
                mock.patch.object(gb, "_tighten", lambda *a, **k: None), \
                mock.patch.object(gb, "_respace", lambda *a, **k: None), \
                mock.patch.object(gb, "WRITER_THINK", True):
            post, doubts = gb._clarity_pass(_post("start"), "ask", {}, "post105")
        return post, doubts, calls

    def test_cap_is_retried_without_thinking(self):
        cap = gb.llm.LLMError("Answer ran past its 16000-token cap")
        post, doubts, calls = self._run([cap, _post("good")])
        self.assertEqual(post["tag"], "good")
        self.assertEqual(doubts, [])
        self.assertEqual([c["think"] for c in calls], [True, False])
        self.assertTrue(calls[1]["label"].endswith("/nothink"))

    def test_next_round_is_told_the_broken_rule(self):
        post, doubts, calls = self._run([_post("bad"), _post("good")])
        self.assertEqual(post["tag"], "good")
        first = calls[0]["messages"][-1]["content"]
        second = calls[1]["messages"][-1]["content"]
        self.assertNotIn("NOT KEPT", first)
        self.assertIn("NOT KEPT", second)
        self.assertIn("gobierno", second)

    def test_two_caps_end_the_pass(self):
        cap = gb.llm.LLMError("cap")
        post, doubts, calls = self._run([cap, cap])
        self.assertEqual(post["tag"], "start")
        self.assertEqual(len(doubts), 1)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
