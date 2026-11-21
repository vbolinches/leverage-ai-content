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

import factcheck  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
