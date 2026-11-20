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


# Dates written inside running text, for checking a brief's facts against its
# page. Day-level dates only: a bare year or "November 2026" names no day to
# disagree about.
_IN_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_IN_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
_IN_MDY = re.compile(r"\b([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b"
                     r"(?:,?\s+(\d{4})\b)?")
_IN_DMY = re.compile(r"\b(\d{1,2})(?:\s+de)?\s+([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?"
                     r"(?:,?\s+(?:del?\s+)?(\d{4})\b)?", re.I)


_ABBR = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept",
         "oct", "nov", "dec", "ene", "abr", "ago", "dic"}


def _month_in_text(word):
    """A month only when the word is one: a full name or a standard short
    form. Running text is full of words that start like a month ('set',
    'mar', 'jun-'), and English 'may' is a verb unless it is capitalised."""
    w = word.lower().rstrip(".")
    if w == "may" and word[0].islower():
        return None
    if w == "mar" and word[0].islower():
        return None              # Spanish 'mar' is the sea
    full = any(w == n for names in _NAMES.values() for n in names)
    return _month(w) if (full or w in _ABBR) else None


def mentioned(text):
    """Every day-level date in `text`, as (year or None, month, day).

    A numeric date gives both readings (01/02/2027 could be either), and a
    two-digit year is read as 20yy, the way USCIS prints edition dates
    ("11/19/26"). Lenient on purpose: this finds what a page MIGHT say, and
    a fact is only dropped when none of its readings is there.
    """
    out = set()
    s = text or ""
    for y, m, d in _IN_ISO.findall(s):
        if _make(y, m, d):
            out.add((int(y), int(m), int(d)))
    for a, b, y in _IN_NUM.findall(s):
        y = int(y) + (2000 if len(y) == 2 else 0)
        for m, d in ((a, b), (b, a)):
            if _make(y, m, d):
                out.add((y, int(m), int(d)))
    for word, d, y in _IN_MDY.findall(s):
        m = _month_in_text(word)
        if m and _make(y or 2000, m, d):
            out.add((int(y) if y else None, m, int(d)))
    for d, word, y in _IN_DMY.findall(s):
        m = _month_in_text(word)
        if m and _make(y or 2000, m, d):
            out.add((int(y) if y else None, m, int(d)))
    return out


def missing_from(text, page):
    """Dates in `text` that `page` never gives: a dated date must be on the
    page with its year, a yearless one with any year."""
    have = mentioned(page)
    days = {(m, d) for _, m, d in have}
    return sorted((x for x in mentioned(text)
                   if not (x in have if x[0] else (x[1], x[2]) in days)),
                  key=lambda x: (x[0] or 0, x[1], x[2]))
