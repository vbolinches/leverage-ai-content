"""python -m unittest discover -s tests  (from the repo root)

batch-2026-11-21.log: inmigraforma made 0 of 3.

- The PERM post's last problem was one claim the checker quoted across two
  sentences of the plain-words slide ("Qué dice: ... Qué significa: ...").
  The deletion step matched neither sentence alone, removed nothing, re-checked
  the same post for 77 seconds and rejected it.
- Five fix rounds ended on "1 rule(s) broken", and every clarity and truth
  fix "broke the format", without the log ever saying which rule.
- Four briefs were dropped because "the page could not be opened", with no
  page named.
- Vicente asked (Nov 21) for DHS's own news list to be watched as a third
  official source. 45 of its 50 latest releases are arrest round-ups, so only
  titles matching the account's "keep" pattern are offered.
"""
import copy
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ACCOUNT", "inmigraforma")

import json  # noqa: E402
from datetime import date, timedelta  # noqa: E402

import factcheck  # noqa: E402
import newsroom  # noqa: E402
import generate_batch as gb  # noqa: E402
import search  # noqa: E402

CLAIM = ("Qué dice: El gobierno quiere más datos antes de revisar el caso. "
         "Qué significa: Los empleadores tendrán que llenar más cuadros y "
         "subir más papeles")


def _post():
    return {"slides": [
        {"kind": "cover", "headline": "Propuesta PERM", "sub": "Todavía no está en vigor."},
        {"kind": "step", "eyebrow": "EN PALABRAS", "headline": "Qué dice ese texto.",
         "body": [{"t": "Qué dice: El gobierno quiere más datos antes de revisar "
                        "el caso. Qué significa: Los empleadores tendrán que "
                        "llenar más cuadros y subir más papeles. "},
                  {"t": "Si nadie comenta, la propuesta puede quedar tal cual.",
                   "c": "green", "b": True}]},
    ], "caption": "Propuesta del Departamento de Trabajo."}


class ClaimAcrossTwoSentences(unittest.TestCase):
    def test_both_sentences_are_deleted(self):
        cut = factcheck.strip_claims(_post(), [CLAIM])
        self.assertIsNotNone(cut)
        body = cut["slides"][1]["body"]
        self.assertNotIn("más datos", body)
        self.assertNotIn("más papeles", body)
        self.assertIn("Si nadie comenta", body)

    def test_a_true_neighbour_is_kept(self):
        post = _post()
        post["slides"][1]["body"][0]["t"] += "El plazo para comentar es largo. "
        cut = factcheck.strip_claims(post, [CLAIM])
        self.assertIn("El plazo para comentar es largo.", cut["slides"][1]["body"])

    def test_one_sentence_claim_unchanged(self):
        cut = factcheck.strip_claims(
            _post(), ["Los empleadores tendrán que llenar más cuadros y subir más papeles"])
        body = cut["slides"][1]["body"]
        self.assertIn("más datos", body)
        self.assertNotIn("más papeles", body)


class NothingFoundIsNotRechecked(unittest.TestCase):
    def test_no_recheck_when_nothing_was_deleted(self):
        post = _post()
        with mock.patch.object(gb, "validate", return_value=[]), \
                mock.patch.object(gb, "_verify",
                                  return_value=(["UNVERIFIED x"], ["x"])) as ver, \
                mock.patch.object(gb.factcheck, "strip_claims",
                                  side_effect=lambda p, c: copy.deepcopy(p)), \
                mock.patch("builtins.print") as out:
            _, errs = gb._truth_pass(post, "ask", {}, "post105", rounds=0)
        self.assertEqual(ver.call_count, 1)          # the first check only
        self.assertEqual(errs, ["UNVERIFIED x"])
        said = " ".join(str(c.args[0]) for c in out.call_args_list if c.args)
        self.assertIn("nothing deleted", said)


class LogNamesTheRule(unittest.TestCase):
    def test_first_rule_is_clipped(self):
        line = gb._first(["slide 3: the headline says 'certificación', " + "x" * 300,
                          "slide 4: too long"])
        self.assertTrue(line.startswith("slide 3"))
        self.assertIn("+1 more", line)
        self.assertLess(len(line), 200)

    def test_unopened_page_is_named(self):
        url = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
        with mock.patch.object(search, "fetch"), \
                mock.patch.dict(search.PAGES, {}, clear=True):
            why = search.supports(url, "x" * 60)
        self.assertIn("travel.state.gov", why)


def _dhs_row(day, path, title, summary="WASHINGTON - Summary."):
    return (f'<li class="usa-collection__item"><a href="https://www.dhs.gov{path}">'
            f'<time datetime="{day}"><span>x</span></time></a>'
            f'<div class="usa-collection__body"><h3 class="usa-collection__heading">'
            f'\n      <a href="https://www.dhs.gov{path}" hreflang="en">{title}</a>'
            f'</h3><p>{summary}</p></div></li>')


class DhsNewsList(unittest.TestCase):
    def setUp(self):
        today = date.today()
        self.page = "<ul class=\"usa-collection\">" + "".join([
            _dhs_row(today.isoformat(), "/news/2026/11/19/e-verify-contracts",
                     "DHS and GSA Identify Contracts Noncompliant with E-Verify",
                     "WASHINGTON &ndash; <strong>E-Verify</strong> review."),
            _dhs_row(today.isoformat(), "/news/2026/11/20/worst-of-the-worst",
                     "WORST OF THE WORST: ICE Arrests Murderers and Drug Traffickers"),
            _dhs_row((today - timedelta(days=90)).isoformat(),
                     "/news/2026/08/01/old-asylum", "Old Asylum Rule"),
        ]) + "</ul>"
        with open(os.path.join(ROOT, "accounts", "inmigraforma",
                               "account.json"), encoding="utf-8") as f:
            acct = json.load(f)
        self.src = [x for x in acct["newsroom"] if x["kind"] == "dhs"][0]

    def test_inmigraforma_watches_dhs(self):
        self.assertIn("e-verify", self.src["keep"])

    def test_keeps_paperwork_news_only(self):
        rows = newsroom.dhs(keep=self.src["keep"], page=self.page)
        self.assertEqual([r["url"] for r in rows],
                         ["https://www.dhs.gov/news/2026/11/19/e-verify-contracts"])
        self.assertIn("E-Verify review", rows[0]["summary"])

    def test_items_routes_the_dhs_source(self):
        acct = {"newsroom": [self.src]}
        with mock.patch.object(newsroom, "dhs", return_value=[
                {"title": "E-Verify", "url": "https://www.dhs.gov/news/x",
                 "date": date.today().isoformat(), "summary": ""}]) as got:
            out = newsroom.items(acct)
        got.assert_called_once_with(keep=self.src["keep"])
        self.assertEqual(out[0]["url"], "https://www.dhs.gov/news/x")

    def test_the_list_itself_is_not_a_source(self):
        self.assertTrue(gb._listing(
            "https://www.dhs.gov/news-releases/press-releases?items_per_page=50"))
        self.assertFalse(gb._listing(
            "https://www.dhs.gov/news/2026/11/10/dhs-taking-down-asylum-fraudsters"))


if __name__ == "__main__":
    unittest.main()
