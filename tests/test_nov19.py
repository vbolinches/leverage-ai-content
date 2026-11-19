"""python -m unittest discover -s tests  (from the repo root)

batch-2026-11-19.log: inmigraforma made 0 of 4. Two true posts were lost to
rules that misfired, and the reader's thinking ran past its cap twelve times:

- the I-914 post wrote 'T-Visa', which the glued-words rule read as a code
  glued to a word, and five rewrites had nothing to put back;
- the fee calculator recap said 'elegir' where the explanation said 'elija';
- a capped clarity read counted as a doubt, so explanations were rewritten
  for want of a read.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ACCOUNT", "inmigraforma")

import clarity  # noqa: E402
import generate_batch as gb  # noqa: E402
import llm  # noqa: E402

I914 = [
    "USCIS usa la versión del catorce de septiembre de 2026 del formulario I-914 para víctimas de trata desde el veintisiete.",
    "El estatus T, o T-Visa, es el nombre oficial del permiso para víctimas de trata humana.",
    "La nueva versión del formulario I-914 reemplaza la versión vieja del veinte de enero de 2025.",
    "El cambio del formulario I-914 afecta solo a solicitudes nuevas después del veintisiete de noviembre.",
    "Revisa la fecha de edición en la parte inferior del documento para usar la versión correcta.",
]
FEES = [
    "USCIS actualizó la calculadora de tarifas, la página web oficial de precios, en noviembre de 2026.",
    "La calculadora de tarifas es la herramienta en línea que muestra el costo exacto de cada trámite.",
    "La calculadora de tarifas ayuda a evitar que el gobierno rechace su solicitud, como una visa, por pagar mal el precio.",
    "La actualización de la calculadora de tarifas aplica a los solicitantes que paguen por trámites migratorios.",
    "Visite la página oficial de USCIS, seleccione Español arriba y elija su formulario en la lista para ver el precio.",
]


def slide_errs(post, n):
    errs = gb._clarity_rules(post) + gb._off_script(post)
    return [e for e in errs if e.startswith(f"slide {n}")]


class TVisaIsNotGlued(unittest.TestCase):
    def post(self, headline):
        return {"_sentences": I914, "slides": [
            {"kind": "cover", "headline": "Nuevo formulario I-914",
             "sub": "USCIS usa la versión nueva del formulario I-914."},
            {"kind": "step", "headline": headline,
             "body": "El estatus T es el permiso para víctimas de trata humana."},
            {"kind": "step", "headline": "Qué es la T-Visa",
             "body": "El estatus T es el nombre oficial del permiso para víctimas."}]}

    def test_t_visa_passes(self):
        self.assertEqual([e for e in slide_errs(self.post("La T-Visa"), 3)
                          if "glued" in e], [])

    def test_real_glue_is_still_caught(self):
        for glued in ("H-2Bpara", "I-485para", "H-1BVisa"):
            self.assertTrue(gb._GLUED.search(glued), glued)
        for fine in ("T-Visa", "U-Visa", "I-914", "H-2B para"):
            self.assertFalse(gb._GLUED.search(fine), fine)


class InfinitiveOfAnExplainedVerb(unittest.TestCase):
    def post(self, item):
        return {"_sentences": FEES, "slides": [
            {"kind": "cover", "headline": "Calculadora de tarifas",
             "sub": "USCIS actualizó la calculadora de tarifas en noviembre."},
            {"kind": "recap", "headline": "Lo que puede hacer ahora.",
             "items": [item, "La calculadora de tarifas muestra el costo exacto."]}]}

    def test_elegir_passes_when_the_explanation_says_elija(self):
        post = self.post("Use la calculadora de tarifas para elegir su formulario.")
        self.assertEqual(slide_errs(post, 2), [])

    def test_an_added_word_is_still_refused(self):
        post = self.post("Use la calculadora de tarifas para conseguir un reembolso.")
        self.assertTrue(any("conseguir" in e for e in slide_errs(post, 2)))

    def test_only_forms_of_the_same_verb_count(self):
        words = {"entre", "elija", "pague"}
        self.assertTrue(gb._conjugated("elegir", words))
        self.assertTrue(gb._conjugated("pagar", words) is False)  # five letters: never checked
        self.assertFalse(gb._conjugated("entrevistar", words))
        self.assertFalse(gb._conjugated("formulario", words))


class CappedReadIsRetriedWithoutThinking(unittest.TestCase):
    POST = {"slides": [{"kind": "cover", "headline": "Calculadora de tarifas",
                        "sub": "USCIS actualizó la calculadora de tarifas."}]}
    ACCT = {"slide_language": "es"}

    def test_a_cap_retries_once_without_thinking(self):
        calls = []

        def fake(*a, **k):
            calls.append(k["think"])
            if k["think"]:
                raise llm.LLMError("Answer ran past its 12000-token cap, so it is cut off.")
            return {"i_understood_it_is_about": "fees", "doubts": []}

        with mock.patch.object(llm, "structured", side_effect=fake):
            doubts, _ = clarity.read(self.POST, None, self.ACCT, model="m", label="t")
        self.assertEqual(calls, [True, False])
        self.assertFalse(any("could not run" in d for d in doubts))

    def test_other_failures_are_not_retried(self):
        with mock.patch.object(llm, "structured",
                               side_effect=llm.LLMError("connection refused")) as m:
            doubts, _ = clarity.read(self.POST, None, self.ACCT, model="m", label="t")
        self.assertEqual(m.call_count, 1)
        self.assertTrue(any("could not run" in d for d in doubts))


if __name__ == "__main__":
    unittest.main()
