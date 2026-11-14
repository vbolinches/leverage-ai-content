"""Read the publication date a page carries, in whatever form the model copied it.

The brief schema asks for YYYY-MM-DD, and the local model mostly obeys - until
it doesn't. On 2026-11-14 it copied six dates the way USCIS prints them
("Oct 20, 2026", "Sep 30, 2026"), and _not_news() read only ISO, so all six
briefs were dropped as "unreadable date". A date the model copied faithfully
from the page is the most trustworthy form it can take; refusing it because it
is not in our format punishes the right behaviour.

parse_published() accepts the forms official sources actually print:

    2026-10-20 / 2026-10-20T14:00:00Z     ISO, with or without a time
    2026-10                                ISO month
    Oct 20, 2026 / October 20, 2026        US month-day-year (with or without
    Oct. 20 2026 / Sept. 9, 2026           the comma or the period)
    20 Oct 2026 / 20 October, 2026         day-month-year, English
    20 de octubre de 2026 / 20 oct. 2026   Spanish
    10/20/2026 / 2026/10/20                numeric, only when unambiguous
    September 2026 / septiembre de 2026    month and year (Visa Bulletin)

A month-only date counts as the 1st of that month. A numeric date such as
05/06/2026 that reads as a real date both month-first and day-first is
refused rather than guessed: a wrong guess can move a story six months.

No third-party imports: the batch runs on a bare Python install.
"""
import re
from datetime import date, datetime

# Full month names, English and Spanish ("setiembre" is the Southern Cone
# spelling). A word counts as a month when it is one of these or a prefix of
# at least three letters - "Oct", "Sept", "dic" - so a typo like "Octember"
# is refused rather than read as October.
_NAMES = {
    1: ("january", "enero"), 2: ("february", "febrero"), 3: ("march", "marzo"),
    4: ("april", "abril"), 5: ("may", "mayo"), 6: ("june", "junio"),
    7: ("july", "julio"), 8: ("august", "agosto"),
    9: ("september", "septiembre", "setiembre"), 10: ("october", "octubre"),
    11: ("november", "noviembre"), 12: ("december", "diciembre"),
}

_ISO = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?![\d])")
_YMD_SLASH = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_MONTH = r"([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?"
# Oct 20, 2026 / October 20 2026 / Oct. 20th, 2026
_MDY = re.compile(r"^" + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", re.I)
# 20 Oct 2026 / 20 de octubre de 2026 / 20 oct., 2026
_DMY = re.compile(r"^(\d{1,2})(?:\s+de)?\s+" + _MONTH + r",?(?:\s+de(?:l)?)?\s+(\d{4})$",
                  re.I)
# September 2026 / septiembre de 2026
_MY = re.compile(r"^" + _MONTH + r",?(?:\s+de(?:l)?)?\s+(\d{4})$", re.I)
# A leading weekday adds nothing: "Monday, October 20, 2026", "lunes, 20 de ..."
# Whole names or standard abbreviations only, and a comma required, so that
# "Mar 5, 2026" and "March 2026" are never mistaken for "martes".
_WEEKDAY = re.compile(
    r"^(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|"
    r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\.?,\s+", re.I)


def _month(word):
    w = (word or "").lower()
    if len(w) < 3:
        return None
    for num, names in _NAMES.items():
        if any(name.startswith(w) for name in names):
            return num
    return None


def _make(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def parse_published(raw):
    """The date in `raw`, or None if it cannot be read with confidence."""
    if not raw or not isinstance(raw, str):
        return None
    s = " ".join(raw.strip().split())
    # Labels the model sometimes keeps from the page.
    s = re.sub(r"^(?:published|posted|updated|release date|date|fecha|"
               r"publicado(?: el)?|actualizado(?: el)?)\s*[:\-]?\s*", "", s,
               flags=re.I)
    s = _WEEKDAY.sub("", s).strip().rstrip(".")

    m = _ISO.match(s)
    if m:
        return _make(m.group(1), m.group(2), m.group(3) or 1)

    m = _YMD_SLASH.match(s)
    if m:
        return _make(*m.groups())

    m = _NUMERIC.match(s)
    if m:
        a, b, y = (int(g) for g in m.groups())
        month_first, day_first = _make(y, a, b), _make(y, b, a)
        if month_first and day_first and month_first != day_first:
            return None              # 05/06/2026: May 6 or June 5? Don't guess.
        return month_first or day_first

    m = _MDY.match(s)
    if m and _month(m.group(1)):
        return _make(m.group(3), _month(m.group(1)), m.group(2))

    m = _DMY.match(s)
    if m and _month(m.group(2)):
        return _make(m.group(3), _month(m.group(2)), m.group(1))

    m = _MY.match(s)
    if m and _month(m.group(1)):
        return _make(m.group(2), _month(m.group(1)), 1)

    # RFC 2822 from feeds: "Tue, 20 Oct 2026 14:00:00 GMT" (weekday already cut).
    for fmt in ("%d %b %Y %H:%M:%S %Z", "%d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
