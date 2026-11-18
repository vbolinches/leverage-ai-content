"""python -m unittest discover -s tests  (from the repo root)

batch-2026-11-18.log ran on the Nov 17 code (the fixes were not yet on main).
These tests pin what that night lost:

- inmigraforma's PERM post was rejected after five rewrites over one recap
  arrow, 'Lea el resumen.' (three words);
- the CBP One parole brief cited the USCIS alerts listing, not a page about
  the change (the Nov 17 listing fix already drops it);
- two leverageai posts were rejected because the recap headline was the
  schema's own example, 'Do this today', which the filler rule refuses.
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

PERM_SENTENCES = [
    "El Departamento de Trabajo propone cambiar el trámite de certificación laboral PERM.",
    "La certificación laboral es el permiso que un empleador necesita para patrocinar a un trabajador extranjero.",
    "La propuesta todavía no está vigente y el público puede enviar comentarios.",
    "El aviso oficial explica qué documentos tendría que guardar el empleador.",
    "Si su caso depende de PERM, consulte a un abogado de inmigración.",
]
CHATGPT_SENTENCES = [
    "ChatGPT can now draft a sales proposal from your meeting notes.",
    "You paste the notes and ChatGPT writes a first draft you can edit.",
    "The feature is available today for paid ChatGPT accounts.",
]


def recap(headline, items):
    return {"kind": "recap", "eyebrow": "RECAP", "headline": headline,
            "items": items, "cta_title": "Save this for later",
            "cta_sub": "x", "footer_right": "SAVE THIS"}


def recap_errs(post):
    errs = gb._clarity_rules(post) + gb._off_script(post)
    return [e for e in errs if e.startswith("slide 2")]


class PermRecapArrow(unittest.TestCase):
    def post(self, items):
        return {"_sentences": PERM_SENTENCES, "slides": [
            {"kind": "cover", "headline": "Cambios al trámite PERM",
             "sub": "El Departamento de Trabajo propone cambiar el trámite."},
            recap("La propuesta todavía no está vigente.", items)]}

    def test_the_rejected_arrow_is_still_refused(self):
        post = self.post(["La propuesta todavía no está vigente.",
                          "Lea el resumen.",
                          "Consulte a un abogado de inmigración."])
        self.assertTrue(any("Lea el resumen" in e for e in recap_errs(post)))

    def test_settle_replaces_it_with_an_arrow_that_passes(self):
        post = self.post(["La propuesta todavía no está vigente.",
                          "Lea el resumen.",
                          "Consulte a un abogado de inmigración."])
        self.assertTrue(gb._settle_recap(post))
        items = post["slides"][1]["items"]
        self.assertEqual(len(items), 3)
        self.assertIn(gb._SAFE_ARROW["es"], items)
        self.assertEqual(recap_errs(post), [])

    def test_a_second_fragment_is_dropped_not_duplicated(self):
        post = self.post(["La propuesta todavía no está vigente.",
                          "Lea el resumen.", "Guarde esto."])
        gb._settle_recap(post)
        items = post["slides"][1]["items"]
        self.assertEqual(items.count(gb._SAFE_ARROW["es"]), 1)
        self.assertEqual(len(items), 2)

    def test_a_clean_recap_is_left_alone(self):
        post = self.post(["La propuesta todavía no está vigente.",
                          "Consulte a un abogado de inmigración."])
        self.assertFalse(gb._settle_recap(post))

    def test_formal_lea_needs_to_say_where(self):
        post = self.post(["Lea el aviso oficial del gobierno."])
        self.assertTrue(any("without saying where" in e for e in recap_errs(post)))


class RecapHeadlineFiller(unittest.TestCase):
    def post(self, headline):
        return {"_sentences": CHATGPT_SENTENCES, "slides": [
            {"kind": "cover", "headline": "ChatGPT drafts proposals",
             "sub": "ChatGPT can now draft a sales proposal for you."},
            recap(headline, ["Paste your meeting notes into ChatGPT.",
                             "Edit the first draft it writes.",
                             "Send the proposal when it reads right."])]}

    def test_the_schema_no_longer_shows_a_filler_headline(self):
        import re
        for hl in re.findall(r'"kind":"recap"[^}]*?"headline":"([^"]+)"', gb.SCHEMA):
            self.assertNotIn(re.sub(r"[^\w\s]", "", hl).strip().lower(), gb._FILLER)

    def test_copied_example_is_replaced_and_passes(self):
        for hl in ("Do this today", "Do this today."):
            post = self.post(hl)
            self.assertTrue(any("says nothing" in e for e in recap_errs(post)))
            with mock.patch.dict(gb.ACCT, {"slide_language": "en"}):
                self.assertTrue(gb._settle_recap(post))
            self.assertEqual(post["slides"][1]["headline"], gb._SAFE_RECAP["en"])
            self.assertEqual(recap_errs(post), [])

    def test_english_safe_arrow_passes(self):
        post = self.post("What you can do now.")
        post["slides"][1]["items"][1] = "Read it."
        with mock.patch.dict(gb.ACCT, {"slide_language": "en"}):
            gb._settle_recap(post)
        self.assertIn(gb._SAFE_ARROW["en"], post["slides"][1]["items"])
        self.assertEqual(recap_errs(post), [])

    def test_spanish_safe_headline_passes(self):
        post = {"_sentences": PERM_SENTENCES, "slides": [
            {"kind": "cover", "headline": "Cambios al trámite PERM",
             "sub": "El Departamento de Trabajo propone cambiar el trámite."},
            recap("Resumen", ["La propuesta todavía no está vigente.",
                              "Consulte a un abogado de inmigración."])]}
        gb._settle_recap(post)
        self.assertEqual(post["slides"][1]["headline"], gb._SAFE_RECAP["es"])
        self.assertEqual(recap_errs(post), [])


class CbpOneBrief(unittest.TestCase):
    def test_the_alerts_listing_is_dropped(self):
        self.assertTrue(gb._listing("https://www.uscis.gov/newsroom/alerts"))


if __name__ == "__main__":
    unittest.main()
