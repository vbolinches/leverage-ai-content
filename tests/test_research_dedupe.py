"""python -m unittest discover -s tests  (from the repo root)

Research kept briefing stories inmigraforma had already posted. These tests
pin the fix: pages that already have a post are kept out of what the model is
shown, and the tool loop stops before the brief step overflows the window.
The URLs and topics are the ones batch-2026-11-15.log and -16.log dropped.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ACCOUNT", "inmigraforma")

import generate_batch as gb  # noqa: E402
import llm  # noqa: E402
import newsroom  # noqa: E402
import search  # noqa: E402

SILEIRI = ("https://www.uscis.gov/newsroom/alerts/litigation-related-update-"
           "sileiri-doe-class-members-may-request-reopening-or-reconsideration-of-an")
PENALTIES = ("https://www.federalregister.gov/documents/2026/10/14/2026-21024/"
             "imposition-and-collection-of-civil-penalties-for-certain-"
             "immigration-related-violations")
CHARLOTTE = "https://www.uscis.gov/newsroom/alerts/uscis-opens-asylum-office-in-charlotte"
ENFORCEMENT = ("https://www.uscis.gov/newsroom/news-releases/under-president-"
               "trumps-leadership-uscis-takes-unprecedented-22-million-"
               "enforcement-actions")
FEES = ("https://www.federalregister.gov/documents/2026/10/01/2026-20186/"
        "certain-dhs-immigration-related-fees-required-by-hr-1-fiscal-year-"
        "2027-adjustments-for-inflation")
NEW = "https://www.uscis.gov/newsroom/alerts/a-story-nobody-has-posted"

# What the official listing offered on the 16th: four items, all posted.
LISTING_NOV16 = [
    {"title": "Sileiri Doe class members may request reopening", "url": SILEIRI,
     "date": "2026-10-30", "summary": ""},
    {"title": "Rule: civil penalties for failure to depart", "url": PENALTIES,
     "date": "2026-10-14", "summary": ""},
    {"title": "USCIS opens asylum office in Charlotte", "url": CHARLOTTE,
     "date": "2026-10-20", "summary": ""},
    {"title": "USCIS takes 22 million enforcement actions", "url": ENFORCEMENT,
     "date": "2026-11-05", "summary": ""},
]


def real_covered():
    return gb.used_sources(gb.load_queue())


class Canonical(unittest.TestCase):
    def test_spellings_of_one_page_match(self):
        same = [CHARLOTTE, CHARLOTTE + "/", CHARLOTTE.replace("https", "http"),
                CHARLOTTE.replace("www.", ""), CHARLOTTE + "#main",
                CHARLOTTE + "?utm_source=x&utm_medium=y"]
        self.assertEqual({search.canonical(u) for u in same},
                         {search.canonical(CHARLOTTE)})

    def test_federal_register_short_link_is_the_same_document(self):
        self.assertEqual(search.canonical(PENALTIES),
                         search.canonical("https://www.federalregister.gov/d/2026-21024"))
        self.assertNotEqual(search.canonical(PENALTIES), search.canonical(FEES))

    def test_a_real_query_is_kept(self):
        self.assertNotEqual(search.canonical(NEW + "?id=1"),
                            search.canonical(NEW + "?id=2"))


class PostedPagesFromTheRealQueue(unittest.TestCase):
    def test_every_page_the_logs_dropped_as_covered_is_known(self):
        covered = real_covered()
        for url, post in [(SILEIRI, "post97"), (PENALTIES, "post91"),
                          (CHARLOTTE, "post9"), (ENFORCEMENT, "post101"),
                          (FEES, "post96")]:
            self.assertTrue(covered.get(search.canonical(url), "").startswith(post),
                            url)


class Newsroom(unittest.TestCase):
    def test_nov16_listing_offers_nothing_already_posted(self):
        with mock.patch.object(newsroom, "items", return_value=LISTING_NOV16):
            text, urls, hidden = newsroom.digest(gb.ACCT, real_covered())
        self.assertEqual(urls, [])
        self.assertEqual(len(hidden), 4)
        self.assertIn("already has a post", text)
        for url in (SILEIRI, PENALTIES, CHARLOTTE, ENFORCEMENT):
            self.assertNotIn(url, text)

    def test_a_new_item_is_still_offered(self):
        rows = LISTING_NOV16 + [{"title": "Something new", "url": NEW,
                                 "date": "2026-11-15", "summary": "s"}]
        with mock.patch.object(newsroom, "items", return_value=rows):
            text, urls, hidden = newsroom.digest(gb.ACCT, real_covered())
        self.assertEqual(urls, [NEW])
        self.assertIn(NEW, text)
        self.assertNotIn(CHARLOTTE, text)


class Tools(unittest.TestCase):
    def setUp(self):
        search.COVERED.clear()
        search.COVERED.update(real_covered())

    def tearDown(self):
        search.COVERED.clear()

    def test_a_posted_page_is_not_read_into_the_conversation(self):
        with mock.patch.object(search, "fetch", side_effect=AssertionError("fetched")):
            out = search.dispatch("read_page", {"url": CHARLOTTE + "/"})
        self.assertIn("already posted", out)
        self.assertLess(len(out), 200)

    def test_a_new_page_is_still_read(self):
        with mock.patch.object(search, "fetch", return_value="URL: x\n\npage") as f:
            search.dispatch("read_page", {"url": NEW})
        f.assert_called_once_with(NEW)

    def test_search_results_leave_out_posted_pages(self):
        hits = [{"title": "Charlotte", "url": CHARLOTTE, "snippet": "s"},
                {"title": "New", "url": NEW, "snippet": "s"}]
        with mock.patch.object(search, "search", return_value=hits):
            out = search.dispatch("web_search", {"query": "uscis"})
        self.assertIn(NEW, out)
        self.assertNotIn(CHARLOTTE, out)
        with mock.patch.object(search, "search", return_value=hits[:1]):
            out = search.dispatch("web_search", {"query": "uscis"})
        self.assertIn("already posted", out)


class ToolLoopBudget(unittest.TestCase):
    def fake_chat(self, prompts):
        it = iter(prompts)

        def chat(convo, **kw):
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "read_page",
                                                 "arguments": {"url": NEW}}}],
                    "_prompt_tokens": next(it), "_num_ctx": 32768}
        return chat

    def test_stops_on_what_the_next_call_will_hold(self):
        # 20K prompt is under the 22.9K ceiling, but a 12,000-character page
        # (~4K tokens) just went in after it: the next call would be over.
        page = "x" * 12000
        with mock.patch.object(llm, "chat", self.fake_chat([20000, 30000])), \
                mock.patch("builtins.print"):
            convo, calls = llm.tool_loop("s", "u", [], lambda n, a: page,
                                         max_rounds=10)
        self.assertEqual(calls, 1)

    def test_small_rounds_keep_going(self):
        with mock.patch.object(llm, "chat", self.fake_chat([4000, 5000, 6000])), \
                mock.patch("builtins.print"):
            _, calls = llm.tool_loop("s", "u", [], lambda n, a: "short",
                                     max_rounds=3)
        self.assertEqual(calls, 3)


class ResearchReplayNov16(unittest.TestCase):
    """Sweep after sweep the model handed back the same four posted stories."""

    BRIEFS = [
        ("Sileiri Doe class members can request reopen", SILEIRI),
        ("Civil penalties for failing to depart during", PENALTIES),
        ("USCIS opens asylum office in Charlotte", CHARLOTTE),
        ("USCIS enforcement actions under Trump admini", ENFORCEMENT),
    ]

    def test_posted_briefs_cost_no_fetch_and_go_on_the_avoid_list(self):
        asks, menus = [], []

        def tool_loop(system, ask, *a, **kw):
            asks.append(ask)
            # The model's own searches turned these pages up.
            for _, url in self.BRIEFS:
                search.RETRIEVED.add(url)
            search.RETRIEVED.add(NEW)
            return [{"role": "user", "content": ask}], 1

        def structured(*a, messages=None, **kw):
            menus.append(messages[-1]["content"])
            return {"briefs": [
                {"topic": t, "source_url": u, "facts": ["a", "b"],
                 "quote": "q" * 50, "published": "2026-10-20",
                 "audience": "broad", "why_now": "now"}
                for t, u in self.BRIEFS]}

        boom = AssertionError("a posted page was fetched")
        with mock.patch.object(newsroom, "items", return_value=LISTING_NOV16), \
                mock.patch("ideas.digest", return_value=""), \
                mock.patch.object(llm, "tool_loop", tool_loop), \
                mock.patch.object(llm, "structured", structured), \
                mock.patch.object(search, "supports", side_effect=boom), \
                mock.patch.object(search, "fetch", side_effect=boom), \
                mock.patch("builtins.print"):
            briefs = gb.research(3, ["post102-dhs-campaña-fraude-asilo"])

        self.assertEqual(briefs, [])
        # The official listing no longer offers them, in any sweep.
        self.assertIn("already has a post", asks[0])
        for ask in asks:
            for _, url in self.BRIEFS:
                self.assertNotIn(url, ask)
        # Nor does the menu the briefs are chosen from.
        for menu in menus:
            for _, url in self.BRIEFS:
                self.assertNotIn(url, menu)
        # A second sweep is told what the first one wasted itself on.
        for topic, _ in self.BRIEFS:
            self.assertIn(topic, asks[1])


if __name__ == "__main__":
    unittest.main()
