"""python -m unittest discover -s tests  (from the repo root)

The leverageai run of 2026-11-16 wrote 0 of 3 posts. Two causes, both covered
here without a model server: every call that would reach Ollama is stubbed.

1. The clarity reader ran with reasoning on and greedy decoding, which qwen3's
   model card says loops; 82 of 126 reads on Nov 12-16 hit the 12,000-token
   cap, and each crash counted as a doubt.
2. Brief facts were never checked against their page. The reader demanded
   them, the fact-check struck them, six rounds per topic.

The three source pages are in tests/fixtures, as read on 2026-11-16. The brief
facts below are reconstructed from that night's log (the log prints what the
reader said the post was "meant" to say, not the brief itself).
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm        # noqa: E402
import clarity    # noqa: E402
import factcheck  # noqa: E402
import search     # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
URLS = {
    "gemini-4-hydrogen-search-upgrade":
        "https://blog.google/products-and-platforms/products/search/gemini-4-hydrogen-search-upgrade/",
    "gemini-4-hydrogen-model-card":
        "https://deepmind.google/models/model-cards/gemini-4-hydrogen/",
    "adobe-google-partnership-integration":
        "https://blog.google/company-news/inside-google/company-announcements/adobe-google-partnership-integration/",
}
ACCT = {"slug": "leverageai"}   # no require_source_url, as on leverageai


def page(name):
    with open(os.path.join(FIX, name + ".txt"), encoding="utf-8") as f:
        return f.read()


def load_pages():
    for name, url in URLS.items():
        search.PAGES[url] = page(name)


# ----------------------------------------------------------------- sampling

class ThinkingNeverGreedy(unittest.TestCase):
    def payload(self, **kw):
        seen = {}

        def fake_post(path, payload, timeout):
            seen.update(payload)
            return {"message": {"content": "{}"}, "done_reason": "stop"}

        with mock.patch.object(llm, "_post", fake_post), \
                mock.patch.object(llm, "log_usage", lambda *a, **k: None):
            llm.chat([{"role": "user", "content": "x"}], **kw)
        return seen["options"]

    def test_think_with_temperature_zero_uses_documented_sampling(self):
        o = self.payload(think=True, temperature=0, max_tokens=12000)
        self.assertEqual(o["temperature"], 0.6)
        self.assertEqual((o["top_p"], o["top_k"], o["min_p"]), (0.95, 20, 0.0))
        # Repeatable: the same seed every call, unlike temperature > 0 calls.
        self.assertEqual(o["seed"], llm.THINK_SEED)
        self.assertEqual(self.payload(think=True, temperature=0)["seed"],
                         llm.THINK_SEED)

    def test_no_think_temperature_zero_stays_greedy(self):
        o = self.payload(think=False, temperature=0, max_tokens=600)
        self.assertEqual(o["temperature"], 0)
        self.assertNotIn("top_k", o)
        self.assertNotIn("seed", o)

    def test_explicit_temperature_untouched(self):
        o = self.payload(think=True, temperature=0.3)
        self.assertEqual(o["temperature"], 0.3)
        self.assertNotIn("top_k", o)
        self.assertNotEqual(o.get("seed"), llm.THINK_SEED)


# ------------------------------------------------------------ clarity read

# One of Nov 16's explanations (post115/explain6, the Gemini 4 Hydrogen in
# Sheets topic), as the reader saw it.
SENTS = [
    "The Gemini 4 Hydrogen artificial intelligence model launched on November 12, 2026.",
    "Tokens are the small text units that measure how much work the artificial intelligence performs.",
    "The Gemini 4 Hydrogen model is available in Google Sheets, an online spreadsheet program.",
    "Downstream software providers may use the Gemini 4 Hydrogen model through an API, a program link.",
    "The full safety report for the Gemini 4 Hydrogen model will appear online in the coming days.",
]
PROBE = {"slides": [{"kind": "step", "body": s} for s in SENTS]}
CLEAR = {"i_understood_it_is_about": "a new Gemini model in Sheets",
         "i_think_it_is_for": "spreadsheet users", "what_i_would_do_now": "try it",
         "doubts": [], "matches_intended_message": "yes",
         "what_i_got_wrong_or_missed": "nothing"}
CAP = llm.LLMError("Answer ran past its 12000-token cap, so it is cut off. "
                   "The caller asked for something short and got something long.")


class ClarityRunaway(unittest.TestCase):
    def test_runaway_is_read_again_without_reasoning(self):
        calls = []

        def fake(*a, think=False, **k):
            calls.append(think)
            if think:
                raise CAP
            return dict(CLEAR)

        with mock.patch.object(llm, "structured", fake):
            doubts, rep = clarity.read(PROBE, {"topic": "t", "facts": []}, ACCT,
                                       label="post115/explain6")
        self.assertEqual(calls, [True, False])
        self.assertEqual(doubts, [])     # Nov 16: "could not run" = 1 doubt
        self.assertEqual(rep["matches_intended_message"], "yes")

    def test_clear_read_needs_one_call(self):
        calls = []

        def fake(*a, think=False, **k):
            calls.append(think)
            return dict(CLEAR)

        with mock.patch.object(llm, "structured", fake):
            doubts, _ = clarity.read(PROBE, None, ACCT, label="x")
        self.assertEqual((calls, doubts), ([True], []))

    def test_both_failing_is_still_reported(self):
        def fake(*a, think=False, **k):
            raise CAP

        with mock.patch.object(llm, "structured", fake):
            doubts, _ = clarity.read(PROBE, None, ACCT, label="x")
        self.assertEqual(len(doubts), 1)
        self.assertIn("could not run", doubts[0])

    def test_other_errors_do_not_retry(self):
        calls = []

        def fake(*a, think=False, **k):
            calls.append(think)
            raise llm.LLMError("Cannot reach Ollama")

        with mock.patch.object(llm, "structured", fake):
            doubts, _ = clarity.read(PROBE, None, ACCT, label="x")
        self.assertEqual(calls, [True])
        self.assertIn("could not run", doubts[0])


# --------------------------------------------------------- grounded facts

def checker(verdicts):
    """A stand-in fact-checker: returns the given (claim, verdict, evidence)."""
    def fake(system, user, schema, require=(), label="", **k):
        if label.startswith("recopy"):
            return {"sentence": ""}
        return {"claims": [{"claim": c, "verdict": v, "evidence": e}
                           for c, v, e in verdicts],
                "status": "in_force"}
    return fake


class PagesSayWhatTheLogSays(unittest.TestCase):
    """The fixtures back the diagnosis: the brief claims are not on the pages."""

    def test_no_time_saving_on_the_search_page(self):
        low = page("gemini-4-hydrogen-search-upgrade").lower()
        for s in ("20 minutes", "20+ minutes", "under 5", "client research",
                  "case stud", "pricing"):
            self.assertNotIn(s, low)

    def test_no_price_on_the_model_card(self):
        low = page("gemini-4-hydrogen-model-card").lower()
        for s in ("$", "1.50", "7.50", "per million"):
            self.assertNotIn(s, low)
        self.assertIn("google sheets", low)

    def test_adobe_is_coming_not_live(self):
        self.assertIn("You'll soon have a direct pathway",
                      page("adobe-google-partnership-integration"))


class GroundFacts(unittest.TestCase):
    def setUp(self):
        load_pages()

    def test_search_brief_loses_the_invented_time_saving(self):
        brief = {"topic": "Gemini 4 Hydrogen for Client Research",
                 "source_url": URLS["gemini-4-hydrogen-search-upgrade"],
                 "facts": [
                     "Google brought Gemini 4 Hydrogen to Google Search globally on November 12, 2026.",
                     "Gemini 4 Hydrogen reduces client research time from 20+ minutes to under 5 minutes.",
                     "Freelancers can use it for client research, case studies and pricing data.",
                     "Users can visit Google Search today to ask their most nuanced questions.",
                 ]}
        fake = checker([
            ("Google brought Gemini 4 Hydrogen to Google Search globally on November 12, 2026",
             "supported", "Hydrogen is now available globally in Google Search for all users"),
            ("Gemini 4 Hydrogen reduces client research time from 20+ minutes to under 5 minutes",
             "not_on_page", ""),
            ("Freelancers can use it for client research, case studies and pricing data",
             "not_on_page", ""),
            ("Users can visit Google Search today to ask their most nuanced questions",
             "supported", "You can try these improved capabilities by visiting Google "
                          "Search today to ask your most nuanced questions."),
        ])
        with mock.patch.object(llm, "structured", fake):
            kept, dropped = factcheck.ground_facts(brief, ACCT, label="post115")
        self.assertEqual(len(kept), 2)
        self.assertTrue(any("20+ minutes" in f for f in dropped))
        self.assertTrue(any("case studies" in f for f in dropped))

    def test_model_card_brief_loses_the_price(self):
        brief = {"topic": "Gemini 4 Hydrogen in Google Sheets",
                 "source_url": URLS["gemini-4-hydrogen-model-card"],
                 "facts": [
                     "Gemini 4 Hydrogen is distributed in Google Sheets.",
                     "Gemini 4 Hydrogen costs $1.50 per million input tokens and $7.50 per million output tokens.",
                     "Downstream providers can use Gemini 4 Hydrogen via an application program interface (API).",
                 ]}
        fake = checker([
            ("Gemini 4 Hydrogen is distributed in Google Sheets", "supported",
             "Gemini 4 Hydrogen is distributed in the following channels"),
            ("Gemini 4 Hydrogen costs $1.50 per million input tokens and $7.50 per million output tokens",
             "not_on_page", ""),
            ("Downstream providers can use Gemini 4 Hydrogen via an application program interface (API)",
             "supported", "Our models are available to downstream providers via an "
                          "application program interface (API)"),
        ])
        with mock.patch.object(llm, "structured", fake):
            kept, dropped = factcheck.ground_facts(brief, ACCT, label="post115")
        self.assertEqual(dropped, [brief["facts"][1]])
        self.assertEqual(len(kept), 2)

    def test_supported_with_evidence_not_on_page_is_dropped(self):
        # A "supported" verdict whose quoted proof is not on the real page
        # counts for nothing - the existing on_page rule, applied to facts.
        brief = {"topic": "Adobe in Gmail",
                 "source_url": URLS["adobe-google-partnership-integration"],
                 "facts": ["Adobe Acrobat joined Google Drive and Gmail on November 10, 2026.",
                           "Adobe will bring its creative tools to AI Mode in Google Search.",
                           "Businesses will be able to use Adobe's CX Enterprise Coworker agent in Gemini Enterprise."]}
        fake = checker([
            ("Adobe Acrobat joined Google Drive and Gmail on November 10, 2026",
             "supported", "Adobe Acrobat is now live in Google Drive and Gmail"),
            ("Adobe will bring its creative tools to AI Mode in Google Search",
             "supported", "Adobe will also bring its creative tools to AI Mode in Google Search."),
            ("Businesses will be able to use Adobe's CX Enterprise Coworker agent in Gemini Enterprise",
             "supported", "Businesses will be able to use Adobe's CX Enterprise Coworker agent in Gemini Enterprise"),
        ])
        with mock.patch.object(llm, "structured", fake):
            kept, dropped = factcheck.ground_facts(brief, ACCT, label="post115")
        self.assertEqual(dropped, [brief["facts"][0]])
        self.assertEqual(len(kept), 2)

    def test_checker_outage_removes_nothing(self):
        brief = {"topic": "x", "source_url": URLS["gemini-4-hydrogen-model-card"],
                 "facts": ["a fact about Gemini 4 Hydrogen", "another fact here"]}

        def down(*a, **k):
            raise llm.LLMError("Cannot reach Ollama")

        with mock.patch.object(llm, "structured", down):
            kept, dropped = factcheck.ground_facts(brief, {"require_source_url": True})
        self.assertEqual((kept, dropped), (brief["facts"], []))

    def test_unreadable_page_removes_nothing(self):
        brief = {"topic": "x", "source_url": "https://example.invalid/none",
                 "facts": ["one fact about something", "two facts about something"]}
        with mock.patch.object(search, "fetch", lambda *a, **k: "could not open"):
            kept, dropped = factcheck.ground_facts(brief, ACCT)
        self.assertEqual(dropped, [])


class WritePostStopsEarly(unittest.TestCase):
    """A brief left with fewer than two facts fails in one check, not six
    rounds, so the author loop moves to the next spare."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ACCOUNT", "leverageai")
        global generate_batch
        import generate_batch   # noqa: F401  (needs ACCOUNT)

    def test_raises_before_any_writing(self):
        brief = {"topic": "t", "source_url": "u", "facts": ["a", "b", "c"]}
        with mock.patch.object(factcheck, "ground_facts",
                               lambda *a, **k: (["a"], ["b", "c"])), \
                mock.patch.object(generate_batch, "_explain",
                                  side_effect=AssertionError("wrote anyway")):
            with self.assertRaises(llm.LLMError) as e:
                generate_batch.write_post(brief, "post115", 115)
        self.assertIn("only 1", str(e.exception))

    def test_keeps_only_grounded_facts(self):
        brief = {"topic": "t", "source_url": "u", "facts": ["a", "b", "c"]}
        seen = {}

        def stop(b, *a, **k):
            seen["facts"] = list(b["facts"])
            raise llm.LLMError("stop here")

        with mock.patch.object(factcheck, "ground_facts",
                               lambda *a, **k: (["a", "c"], ["b"])), \
                mock.patch.object(generate_batch, "_explain", stop), \
                mock.patch.object(generate_batch, "_page_steps", lambda u: []), \
                mock.patch.object(llm, "structured",
                                  side_effect=llm.LLMError("stop here")):
            with self.assertRaises(llm.LLMError):
                generate_batch.write_post(brief, "post115", 115)
        self.assertEqual(brief["facts"], ["a", "c"])


if __name__ == "__main__":
    unittest.main()
