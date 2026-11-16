"""python -m unittest discover -s tests  (from the repo root)

Research kept briefing stories inmigraforma had already posted. These tests
pin the fix: pages that already have a post are kept out of what the model is
shown, and the tool loop stops before the brief step overflows the window.
The URLs and topics are the ones batch-2026-11-15.log and -16.log dropped.
"""
import json
import os
import sys
import tempfile
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
        # Charlotte was posted twice, as post89 and post92: either counts.
        for url, posts in [(SILEIRI, ("post97",)), (PENALTIES, ("post91",)),
                           (CHARLOTTE, ("post89", "post92")),
                           (ENFORCEMENT, ("post101",)), (FEES, ("post96",))]:
            self.assertTrue(covered.get(search.canonical(url), "").startswith(posts),
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


INDEX = "https://www.federalregister.gov/index/2026/u-s-citizenship-and-immigration-services"

# Briefs the logs dropped as "already covered", night by night.
DROPPED_AS_COVERED = {
    "2026-11-15": [
        ("Sileiri Doe Class Members Can Request Reopen", SILEIRI),
        ("DHS Fees Adjusted for Inflation", FEES),
        ("Asylum Office Opens in Charlotte", CHARLOTTE),
        ("Civil Penalties for Failing to Depart", PENALTIES),
        ("Alien Change of Address information collection", INDEX),
    ],
    "2026-11-16": [
        ("Sileiri Doe class members can request reopen", SILEIRI),
        ("Civil penalties for failing to depart during", PENALTIES),
        ("USCIS opens asylum office in Charlotte", CHARLOTTE),
        ("USCIS enforcement actions under Trump admini", ENFORCEMENT),
    ],
}


def brief(topic, url):
    return {"topic": topic, "source_url": url, "facts": ["a", "b"],
            "quote": "q" * 50, "published": "2026-10-20",
            "audience": "broad", "why_now": "now"}


class ResearchReplay(unittest.TestCase):
    """Sweep after sweep the model handed back the same posted stories."""

    def replay(self, night):
        dropped = DROPPED_AS_COVERED[night]
        listing = [{"title": t, "url": u, "date": "2026-10-20", "summary": ""}
                   for t, u in dropped]
        asks, menus = [], []

        def tool_loop(system, ask, *a, **kw):
            asks.append(ask)
            # The model's own searches turned these pages up.
            for _, url in dropped:
                search.RETRIEVED.add(url)
            # And one page nobody has posted, so the sweep has a menu.
            search.RETRIEVED.add(f"{NEW}-{night}-{len(asks)}")
            return [{"role": "user", "content": ask}], 1

        def structured(*a, messages=None, **kw):
            menus.append(messages[-1]["content"])
            return {"briefs": [brief(t, u) for t, u in dropped]}

        boom = AssertionError("a posted page was fetched")
        with mock.patch.object(newsroom, "items", return_value=listing), \
                mock.patch("ideas.digest", return_value=""), \
                mock.patch.object(llm, "tool_loop", tool_loop), \
                mock.patch.object(llm, "structured", structured), \
                mock.patch.object(search, "supports", side_effect=boom), \
                mock.patch.object(search, "fetch", side_effect=boom), \
                mock.patch("builtins.print"):
            briefs = gb.research(3, ["post102-dhs-campaña-fraude-asilo"])

        self.assertEqual(briefs, [])
        self.assertIn("already has a post", asks[0])
        for text in asks + menus:
            for _, url in dropped:
                self.assertNotIn(url, text)
        # A second sweep is told what the first one wasted itself on.
        for topic, _ in dropped:
            self.assertIn(topic, asks[1])

    def test_nov15(self):
        self.replay("2026-11-15")

    def test_nov16(self):
        self.replay("2026-11-16")


class OtherWaysIn(unittest.TestCase):
    def setUp(self):
        search.COVERED.clear()
        search.COVERED.update(real_covered())

    def tearDown(self):
        search.COVERED.clear()
        search.LANDED.clear()

    def test_an_address_that_redirects_to_a_posted_page(self):
        short = "https://www.uscis.gov/go/charlotte"
        with mock.patch.object(search, "_get_urllib",
                               return_value=(b"<p>page</p>", "text/html", CHARLOTTE)):
            out = search.dispatch("read_page", {"url": short})
        self.assertIn("already posted", out)
        self.assertTrue(search.covered_by(short))

    def test_a_lead_pointing_at_a_posted_page(self):
        import ideas
        from datetime import date
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ideas.json"), "w", encoding="utf-8") as f:
                json.dump({"updated": date.today().isoformat(), "items": [
                    {"source": "x", "title": "Charlotte", "url": CHARLOTTE},
                    {"source": "x", "title": "New", "url": NEW}]}, f)
            acct = mock.Mock()
            acct.path = lambda *p: os.path.join(d, *p)
            text = ideas.digest(acct, skip=search.covered_by)
        self.assertIn(NEW, text)
        self.assertNotIn(CHARLOTTE, text)

    def test_a_post_whose_source_is_only_in_its_caption(self):
        # post44 predates source_url in specs; its caption names the page.
        url = ("https://www.uscis.gov/newsroom/alerts/uscis-issues-guidance-on-"
               "making-public-charge-inadmissibility-determination")
        self.assertTrue(search.covered_by(url).startswith("post44"))


CTX = 32768


def prompt_tokens(system, messages, tools=None):
    """What Ollama would be sent, at the same pessimistic rate the code uses."""
    n = llm.estimate_tokens(system) + llm.estimate_tokens(
        json.dumps(tools or [], ensure_ascii=False))
    for m in messages:
        n += llm.estimate_tokens(m.get("content"))
        if m.get("tool_calls"):
            n += llm.estimate_tokens(json.dumps(m["tool_calls"], ensure_ascii=False))
        n += 4
    return n


class WorstCaseWindow(unittest.TestCase):
    """Every input at its largest, a model that reads five full pages a round
    for ten rounds: no call may be sent more than the window holds."""

    def test_a_whole_sweep_stays_inside_the_window(self):
        sent = []
        page = "Lorem ipsum dolor sit amet. " * 1000       # 28K chars per page
        counter = iter(range(10 ** 6))

        def chat(messages, system=None, tools=None, schema=None, **kw):
            n = prompt_tokens(system, messages, tools)
            sent.append(("briefs" if schema else "research", n))
            if schema:
                return {"role": "assistant", "content": '{"briefs": []}',
                        "_prompt_tokens": int(n), "_num_ctx": CTX}
            calls = [{"function": {"name": "read_page", "arguments":
                      {"url": f"https://www.uscis.gov/newsroom/alerts/s{next(counter)}"}}}
                     for _ in range(5)]
            return {"role": "assistant", "content": "x" * 3000,
                    "tool_calls": calls, "_prompt_tokens": int(n), "_num_ctx": CTX}

        def fetch(url, max_chars=None):
            search.RETRIEVED.add(url)
            search.TITLES[url] = "T" * 300
            return f"URL: {url}\n\n{page}"

        long = "x" * 400
        office = ("OFFICIAL NEWS\n" + "\n".join(
            f"- 2026-11-0{i % 9 + 1} | {long}\n  https://www.uscis.gov/{long}\n  {long}"
            for i in range(30)), [])
        signals = "\n".join(f"- [x] {long} (https://example.com/{long})"
                            for _ in range(18))
        avoid = [f"post{i}-{'slug' * 20}" for i in range(5000)]
        with mock.patch.object(llm, "chat", chat), \
                mock.patch.object(search, "fetch", fetch), \
                mock.patch("builtins.print"):
            gb._sweep(10, avoid, signals, office)

        research = max(n for kind, n in sent if kind == "research")
        briefs = max(n for kind, n in sent if kind == "briefs")
        # The loop's own budget is 0.7 of the window; tool schemas sit on top.
        self.assertLess(research, 0.75 * CTX, research)
        self.assertLess(briefs, 0.80 * CTX, briefs)
        # And the real tokenizer packs ~3.7 characters per token, so the true
        # prompts are smaller still than these estimates at 3.0.

    def test_fit_drops_oldest_pages_and_keeps_instructions(self):
        msgs = [{"role": "user", "content": "INSTRUCTIONS"}]
        for i in range(10):
            msgs.append({"role": "assistant", "content": f"note {i}"})
            msgs.append({"role": "tool", "content": f"page {i} " + "y" * 9000})
        out = llm.fit("sys", msgs, 12000)
        self.assertLessEqual(prompt_tokens("sys", out), 12000)
        self.assertEqual(out[0]["content"], "INSTRUCTIONS")
        self.assertTrue(out[-1]["content"].startswith("page 9"))
        self.assertTrue(out[2]["content"].startswith("[tool result removed"))
        self.assertTrue(msgs[2]["content"].startswith("page 0"))  # input intact
        with self.assertRaises(llm.LLMError):
            llm.fit("s" * 90000, msgs, 12000)


if __name__ == "__main__":
    unittest.main()
