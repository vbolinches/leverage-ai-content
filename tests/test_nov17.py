"""python -m unittest discover -s tests  (from the repo root)

batch-2026-11-17.log: repeats were gone, but inmigraforma still made 0 of 3
and leverageai lost six briefs. These tests pin the fixes, using that night's
URLs, topics and sentences:

- a Federal Register copy on govinfo.gov was refused as "not official";
- a cover sub could only date itself "Del 17 nov.", which the fragment rule
  refused, because the off-script rule refused "noviembre";
- a HUD final rule was refused six times as "a proposal";
- two of three topics cited a news listing, one a kind of story the account
  skips (arrests);
- leverageai's brief prompt was cut to fit at 3.0 characters per token when
  the model's own count said it fit, so briefs lost the dates they needed.
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ACCOUNT", "inmigraforma")

import factcheck  # noqa: E402
import generate_batch as gb  # noqa: E402
import llm  # noqa: E402
import newsroom  # noqa: E402
import search  # noqa: E402

GOVINFO = "https://www.govinfo.gov/content/pkg/FR-2026-11-02/html/2026-22320.htm"
HUD = ("https://www.federalregister.gov/documents/2026/11/12/2026-23003/housing-"
       "and-community-development-act-of-1980-verification-of-eligible-status")
DHS_LISTING = "https://www.dhs.gov/all-news-updates/180"
USCIS_LISTING = "https://www.uscis.gov/newsroom/alerts"
FEES_GOVINFO = "https://www.govinfo.gov/content/pkg/FR-2026-10-01/html/2026-20186.htm"
GOVINFO_RULE = "https://www.govinfo.gov/content/pkg/FR-2026-11-13/html/2026-23111.htm"

# The explanation post103 (DHS Houston arrests) had checked, from the log.
POST103_SENTENCES = [
    "El comunicado del Departamento de Seguridad Nacional informa sobre arrestos en Houston.",
    "La aplicación de la ley es el trabajo de los agentes para detener a personas que violan las normas.",
    "El aviso oficial destaca acciones de aplicación de la ley y no cambia las reglas migratorias.",
    "El texto del gobierno no nombra a ningún grupo específico ni dice quiénes están seguros o en riesgo.",
    "El aviso publicado no da pasos para actuar, así que debe consultar a un abogado de inmigración.",
]

# A Federal Register final rule as fetch() leaves it: tags gone, label and
# value on separate lines, and the preamble naming the proposal it finishes.
FINAL_RULE_PAGE = """Federal Register :: Housing and Community Development Act of 1980
Rule
AGENCY:

Office of the Assistant Secretary for Public and Indian Housing, HUD.

ACTION:

Final rule.

SUMMARY:

This final rule amends the regulations on verification of eligible status.

DATES:

Effective date: December 14, 2026.

On May 2, 2026, HUD published a proposed rule. The public comment period
closed July 1, 2026, and HUD received 412 written comments.
"""
PROPOSED_RULE_PAGE = FINAL_RULE_PAGE.replace("Final rule.", "Proposed rule.") \
    .replace("Effective date: December 14, 2026.",
             "Comments must be received on or before January 12, 2027.")


class GovinfoIsOfficial(unittest.TestCase):
    def test_govinfo_copy_is_allowed_and_keyed_as_the_fr_document(self):
        self.assertTrue(gb._official(GOVINFO))
        self.assertEqual(search.canonical(GOVINFO), "federalregister.gov/d/2026-22320")
        for u in ("https://www.govinfo.gov/content/pkg/FR-2026-11-02/pdf/2026-22320.pdf",
                  "https://www.govinfo.gov/app/details/FR-2026-11-02/2026-22320"):
            self.assertEqual(search.canonical(u), "federalregister.gov/d/2026-22320")

    def test_govinfo_copy_of_a_posted_rule_is_still_posted(self):
        search.COVERED.clear()
        search.COVERED.update(gb.used_sources(gb.load_queue()))
        try:
            self.assertTrue(search.covered_by(FEES_GOVINFO))
        finally:
            search.COVERED.clear()

    def test_a_govinfo_collection_page_is_a_listing(self):
        self.assertTrue(gb._listing("https://www.govinfo.gov/app/collection/fr"))


class CoverDate(unittest.TestCase):
    def post(self, sub):
        return {"_sentences": POST103_SENTENCES, "slides": [
            {"kind": "cover", "headline": "Arrestos en Houston", "sub": sub}]}

    def slide1(self, errs):
        return [e for e in errs if e.startswith("slide 1")]

    def test_the_rejected_sub_is_still_a_fragment(self):
        errs = gb._clarity_rules(self.post("Del 17 nov."))
        self.assertTrue(any("fragment" in e and "month in full" in e for e in errs))

    def test_a_full_sentence_with_the_month_now_passes(self):
        # Until this fix "noviembre" failed the off-script rule, so the only
        # date the cover could carry was the fragment above.
        sub = "El comunicado sobre arrestos en Houston salió el 17 de noviembre."
        post = self.post(sub)
        self.assertEqual(self.slide1(gb._clarity_rules(post) + gb._off_script(post)), [])

    def test_only_whole_date_words_are_let_through(self):
        # "domingo" is allowed; "dominicanos" shares its stem and is not.
        post = self.post("Los dominicanos fueron arrestados en Houston el domingo.")
        errs = self.slide1(gb._off_script(post))
        self.assertTrue(any("dominicanos" in e for e in errs), errs)


class FinalRuleIsNotAProposal(unittest.TestCase):
    def test_final_rule_page(self):
        self.assertTrue(factcheck.final_rule(FINAL_RULE_PAGE))
        # ...although every old marker is on it:
        low = factcheck._norm(FINAL_RULE_PAGE)
        self.assertTrue(any(m in low for m in factcheck.PROPOSAL_MARKERS))

    def test_proposed_rule_page(self):
        self.assertFalse(factcheck.final_rule(PROPOSED_RULE_PAGE))

    def test_a_page_with_no_action_line_is_not_presumed_final(self):
        self.assertFalse(factcheck.final_rule("We propose to amend the rule. "
                                              "Submit comments by January 5."))

    def check(self, page, status):
        post = {"slides": [{"kind": "cover",
                            "headline": "HUD verifica el estatus migratorio",
                            "sub": "La regla entra en vigor el 14 de diciembre de 2026."}],
                "caption": "La regla de HUD entra en vigor el 14 de diciembre de 2026."}
        data = {"status": status, "claims": [
            {"claim": "La regla entra en vigor el 14 de diciembre de 2026.",
             "verdict": "supported",
             "evidence": "Effective date: December 14, 2026."}]}
        with mock.patch.object(factcheck, "_page_for", return_value=(HUD, page)), \
                mock.patch.object(llm, "structured", return_value=data), \
                mock.patch.object(factcheck, "on_page", return_value=True), \
                mock.patch.object(factcheck, "MIN_SUPPORTED", 1), \
                mock.patch("builtins.print"):
            errs, _ = factcheck.verify_detail(post, {"topic": "HUD"}, gb.ACCT)
        return [e for e in errs if "PROPOSAL" in e]

    def test_check_no_longer_calls_the_hud_final_rule_a_proposal(self):
        self.assertEqual(self.check(FINAL_RULE_PAGE, "in_force"), [])

    def test_check_still_catches_a_real_proposal(self):
        self.assertTrue(self.check(PROPOSED_RULE_PAGE, "in_force"))

    def test_the_models_own_proposed_verdict_still_counts(self):
        self.assertTrue(self.check(FINAL_RULE_PAGE, "proposed"))


def brief(topic, url, published="2026-11-12"):
    return {"topic": topic, "source_url": url, "facts": ["a", "b"],
            "quote": "q" * 50, "published": published, "audience": "broad",
            "why_now": "now"}


class Nov17Briefs(unittest.TestCase):
    """The night's four candidates, through the same filters research uses."""

    def test_replay(self):
        candidates = [
            brief("ICE Arrests Highlighted by DHS", DHS_LISTING, "2026-11-16"),
            brief("USCIS Sileiri Doe Class Members: Reopening Without Fee",
                  USCIS_LISTING, "2026-11-10"),
            brief("HUD Section 8 Housing: Immigration Status Verification Rule", HUD),
            brief("DHS rule on fee waivers", GOVINFO_RULE, "2026-11-13"),
        ]
        pages = {u: "A notice. " * 200 for u in
                 (DHS_LISTING, USCIS_LISTING, HUD, GOVINFO_RULE)}

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
                mock.patch("builtins.print"):
            got = gb.research(3, [])
        self.assertEqual({b["source_url"] for b in got}, {HUD, GOVINFO_RULE})

    def test_listing_pages(self):
        for u in (DHS_LISTING, USCIS_LISTING, "https://www.ice.gov/newsroom",
                  "https://www.justice.gov/news?page=2"):
            self.assertTrue(gb._listing(u), u)
        for u in (HUD, GOVINFO, "https://www.dhs.gov/news/2026/11/17/ice-arrests",
                  "https://www.uscis.gov/newsroom/alerts/uscis-opens-asylum-office-in-charlotte"):
            self.assertFalse(gb._listing(u), u)


CTX = 32768


class MeasuredTokens(unittest.TestCase):
    def test_measured_rate_has_a_margin_and_bounds(self):
        self.assertAlmostEqual(llm.measured_cpt(
            {"_prompt_tokens": 10000, "_prompt_chars": 35000}), 3.15)
        self.assertEqual(llm.measured_cpt(
            {"_prompt_tokens": 10000, "_prompt_chars": 90000}), llm.CPT_MAX)
        self.assertEqual(llm.measured_cpt(
            {"_prompt_tokens": 10000, "_prompt_chars": 10000}), llm.CPT_MIN)
        self.assertEqual(llm.measured_cpt({"_prompt_tokens": 300,
                                           "_prompt_chars": 3000}),
                         llm.CHARS_PER_TOKEN)

    def test_leverageai_brief_prompt_is_not_cut_when_it_fits(self):
        # Sweep 1 on the 17th: the brief call was sent 19,277 tokens of
        # English (~4 characters a token). At 3.0 fit() counted ~25K and cut
        # the oldest pages - the ones the dates came from.
        msgs = [{"role": "user", "content": "INSTRUCTIONS " + "i" * 4000}]
        for i in range(6):
            msgs.append({"role": "assistant", "content": f"note {i}"})
            msgs.append({"role": "tool", "content": f"Published Nov {i + 10}, 2026. "
                         + "word " * 2300})
        target = gb.BRIEF_PROMPT_TOKENS - 2200
        with mock.patch("builtins.print"):
            cut_then = llm.fit("sys", msgs, target)
            cut_now = llm.fit("sys", msgs, target, chars_per_token=3.6)
        self.assertTrue(any(m["content"].startswith("[tool result removed")
                            for m in cut_then))
        self.assertEqual(cut_now, msgs)

    def test_mixed_density_worst_case_stays_in_the_window(self):
        """History in plain prose (4 chars a token) and then pages packed
        with numbers and links (2.6) - the case where a rate measured on the
        past is most wrong about what comes next. Real token counts, as the
        model would report them, must stay inside the window."""
        sent = []
        page = "2026-11-17 https://x.gov/a/1 $1,234 " * 800      # ~28K chars
        counter = iter(range(10 ** 6))

        def true_tokens(system, messages, tools):
            n = len(system or "") / 4.0 + len(json.dumps(tools or [])) / 4.0
            for m in messages:
                c = m.get("content") or ""
                n += len(c) / (2.6 if m.get("role") == "tool" else 4.0)
                if m.get("tool_calls"):
                    n += len(json.dumps(m["tool_calls"])) / 2.6
                n += 4
            return n

        def chat(messages, system=None, tools=None, schema=None, **kw):
            msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
            n = true_tokens(system, messages, tools)
            sent.append(("briefs" if schema else "research", n))
            base = {"role": "assistant", "_prompt_tokens": int(n),
                    "_prompt_chars": llm._chars(msgs), "_num_ctx": CTX}
            if schema:
                return dict(base, content='{"briefs": []}')
            calls = [{"function": {"name": "read_page", "arguments":
                      {"url": f"https://www.uscis.gov/newsroom/alerts/m{next(counter)}"}}}
                     for _ in range(5)]
            return dict(base, content="x" * 3000, tool_calls=calls)

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
        self.assertLess(research, 0.85 * CTX, research)
        self.assertLess(briefs, 0.90 * CTX, briefs)


if __name__ == "__main__":
    unittest.main()
