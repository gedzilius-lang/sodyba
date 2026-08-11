"""Per-portal extraction from alert emails.

Each portal formats its alerts differently, so each gets its own extractor. All
of them return the same listing dict. The generic fallback handles anything
unrecognised — it still finds a price and an area most of the time, and whatever
it misses you fix in the drawer.

KNOWN GAP: these were tested against alert emails reconstructed from what the
portals publish, never against genuine ones. Expect the first real alerts to land
with a field or two missing. Do not rewrite speculatively — wait for a real alert
from each portal, then tighten against actual text.
"""
from __future__ import annotations
import re
from html import unescape
from typing import Any, Callable

from ..config import ALL_MUNICIPALITIES

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\xa0]+")

# The optional decimal tail matters: portals render "60000,00 EUR", and
# without it the currency token no longer follows the digits and nothing
# matches. NBSP and the euro sign are written as escapes so this line stays
# pure ASCII and cannot be mangled by an editor's encoding.
PRICE_RE = re.compile(
    r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,8})(?:[.,]\d{1,2})?\s*(?:EUR|\u20ac|Eur)",
    re.I)
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:kv\.?\s*m|m2|m²)", re.I)
PLOT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:a\b|ar[ųu]|arai|aro)", re.I)
HA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*ha\b", re.I)
# "raj" is a common informal abbreviation alongside "r." and the spelled-out
# "rajon-" forms (rajone/rajono/rajonas) -- e.g. "Lazdijų raj." and even the
# bare "Lazdijų raj" with no period at all, both seen on live rinka.lt
# listings. "raj" is a strict prefix of "rajon", so ordering the alternatives
# would only matter if the chosen branch's matched text fed back into the
# result -- it doesn't: municipality_from() always reformats to "X rajono"
# off of group(1) alone, and group(1) (the place name) is already fixed by
# the time the branch is tried, since \s* has nothing left to backtrack once
# it reaches the non-letter boundary before "r"/"raj"/"rajon". Kept in this
# order (most-specific literal first) as the readable convention regardless.
MUNI_RE = re.compile(r"([A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+)\s*(?:r\.|raj\.?|rajon)", re.U)
CITY_RE = re.compile(r"([A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+)\s*(?:m\.\s*sav|miesto sav)", re.U)
# A bailiff notice carries "Vykdomoji byla Nr. 0157/2024:12" before the real
# "Kadastro Nr. 4152/0007:96", and both fit the bare shape. Leftmost-match
# would take the case number -- and every lot in one execution case shares it,
# so dedupe's authoritative cadastral short-circuit would merge unrelated
# properties. Prefer an explicitly labelled number; fall back to the bare
# shape only when no label is present, skipping case-reference context.
_CAD_NUM = r"(\d{4}[/\-]\d{4}\s*[:\-/]\s*\d{1,4})"
CAD_LABELLED_RE = re.compile(r"(?:kadastr\w*|unikal\w*)[^0-9]{0,24}" + _CAD_NUM, re.I | re.U)
CAD_RE = re.compile(r"\b" + _CAD_NUM + r"\b")
LOCALITY_RE = re.compile(r"([A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+(?:ių|ų|os|ai|iai|ė|as))\s*k\.", re.U)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
URL_RE = re.compile(r"https?://[^\s\"'<>)]+")

# ------------------------------------------------------------------ contacts
# One number, four spellings. Lithuania's national trunk prefix "8" stands in
# for the "+370" country code, so the same seller writes "+37067132403",
# "867132403", "8 671 32403" or "(8-671) 32403" and means one phone. Storing
# whatever the page happened to print would make one seller look like several
# and defeat any comparison, so everything extracted goes through
# normalise_phone() first -- see its docstring for the canonical shape.
#
# The national significant number is always 8 digits and begins 3-6 (3xx/4xx/5
# geographic, 6xx mobile). That leading-digit rule is load-bearing: a pattern
# that accepts "8 followed by eight digits" also accepts the Google Analytics
# property id this very page carries ("UA-128041834-1") and any eight-digit
# price. Separators deliberately exclude the newline, so a run of digits at the
# end of one line cannot be stitched onto the start of the next.
_PHONE_SEP = r"[ \t\u00a0()\-]*"
PHONE_RE = re.compile(
    r"(?<![\w+])(?:(?:\+|00)[ \t]?370|8)" + _PHONE_SEP
    + r"(?:[3-6](?:" + _PHONE_SEP + r"\d){7})(?!\d)", re.U)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def normalise_phone(raw: str | None) -> str | None:
    """A Lithuanian phone number in one canonical shape, or None.

    CANONICAL SHAPE: E.164 -- a literal "+370" followed by the 8-digit
    national number, e.g. "+37067132403". No spaces, no brackets, no trunk
    prefix. Chosen because it is the only spelling that is unambiguous
    internationally, is what `tel:` links already carry, and sorts and
    compares as a plain string.

    Accepted inputs, decided by digit count so the rules cannot overlap:
      8 digits              the national number already ("67132403")
      9 digits starting 8   national trunk form ("867132403", "8 671 32403")
      9 digits starting 0   the same with the wrong trunk digit ("061154699")
      11 digits starting 370  ("+37067132403", "370 671 32403")
      13 digits starting 00370  ("0037067132403")
    Separators of any kind are stripped before counting, so "(8-671) 32403"
    and "8 671 32403" reach the same eight digits.

    The leading-0 form is not correct Lithuanian dialling -- the trunk prefix
    here is 8, not the 0 most of Europe uses -- but sellers type it anyway: one
    of the 35 live listings collected 2026-08-10 publishes "061154699" in the
    portal's own contact widget. It is accepted because this function is only
    ever handed a string the caller already has reason to believe is a phone
    number (a `tel:` href, a data-number attribute, or a PHONE_RE match). The
    free-text scanner PHONE_RE deliberately does NOT accept a bare leading 0:
    in running prose that is a guess, and a guessed phone number gets dialled.

    Anything else -- wrong length, or a national number that does not begin
    3-6 (service and freephone ranges 70x/80x/90x are not seller numbers) --
    comes back None. A number we cannot recognise is not stored in a mangled
    form: None is the honest answer, exactly as in municipality_from().
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 13 and digits.startswith("00370"):
        digits = digits[5:]
    elif len(digits) == 11 and digits.startswith("370"):
        digits = digits[3:]
    elif len(digits) == 9 and digits[0] in "80":
        digits = digits[1:]
    if len(digits) != 8 or digits[0] not in "3456":
        return None
    return "+370" + digits


def phones_in(text: str) -> list[str]:
    """Every recognisable Lithuanian phone number in `text`, canonical and
    deduplicated, in the order they appear."""
    out: list[str] = []
    for m in PHONE_RE.finditer(text or ""):
        n = normalise_phone(m.group(0))
        if n and n not in out:
            out.append(n)
    return out


def emails_in(text: str) -> list[str]:
    """Every email address in `text`, lowercased and deduplicated, in order.

    No fallback and no guessing: a page without an address yields an empty
    list, and the caller stores None. An invented address would be sent mail.
    """
    out: list[str] = []
    for m in EMAIL_RE.finditer(text or ""):
        e = m.group(0).lower().rstrip(".")
        if e not in out:
            out.append(e)
    return out


def to_text(body: str) -> str:
    """Flatten an HTML or plaintext email body into scannable text."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
    # Surface href targets as plain text so URL extraction survives tag stripping.
    t = re.sub(r'(?i)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>', r" \1 ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t)
    t = TAG_RE.sub(" ", t)
    t = unescape(t)
    t = WS_RE.sub(" ", t)
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


def _f(m: re.Match | None, idx: int = 1) -> float | None:
    if not m:
        return None
    raw = m.group(idx).replace("\u00a0", "").replace(" ", "")
    if raw.count(".") == 1 and len(raw.split(".")[1]) == 3:
        raw = raw.replace(".", "")          # 17.000 -> 17000
    return float(raw.replace(",", "."))


# Words that mark a reference number which is NOT a cadastral number:
# bailiff case files, acts, contracts, invoices, rulings. Matched at a word
# boundary (\b), not as a substring -- rounds 1-2 chased individual stems
# hiding inside unrelated words one at a time ("akt" inside "kontaktai";
# "vykdom" inside "vykdomi", works-in-progress; "sutar"/"nutar" inside
# "sutarta"/"nutare", price-agreed/seller-decided) and that is the wrong
# shape of fix -- any stem short enough to catch declensions is also short
# enough to sit inside an unrelated word. \b anchors the match to a real
# word start, so a short stem is safe there ("byl" no longer needs spelling
# out) -- but \b alone does not help where the *unwanted* word itself starts
# with the same letters as the wanted one: "vykdomi" starts with "vykdom"
# exactly like "vykdomoji" does, and "sutarta"/"nutarta" start with
# "sutart"/"nutart" exactly like "sutartis"/"nutartis" do. Those keep their
# full literal forms; "vykdom" is dropped outright, since every real
# "Vykdomoji byla" mention already carries the noun "byla" alongside it.
# "aktu" (instrumental) is dropped too: it is a real prefix of "aktualu"
# ("current/relevant" -- common listing language, e.g. "pasiūlymas
# aktualus"), and genitive "Akto Nr." is the form that actually occurs
# before a reference number, per the coordinator's own worked example.
# Verified empirically against a purpose-built false-positive/false-negative
# check (kontaktai/kontaktas/kontakte/kontaktu/kontakto, faktas/faktu,
# traktoriaus/traktoriumi, vykdomi/vykdoma/vykdomas, sutarta/sutarimas,
# nutarė/nutariau/nutarta, aktualu/aktualus/aktyvus -- none trigger; every
# genuine declined form of byla/aktas/sutartis/sąskaita/nutartis/nutarimas
# still does) before this pattern replaced the substring check.
_CAD_CONTEXT_NOISE_RE = re.compile(
    r"\b(?:byl\w*"                                                  # case
    r"|akto\w*|aktas\w*|akte\w*"                                    # deed/act
    r"|sutartis\w*|sutarties\w*|sutartimi\w*|sutartyje\w*"          # contract
    r"|sąsk\w*"                                                     # invoice
    r"|nutartis\w*|nutarties\w*|nutarimas\w*|nutarimo\w*|nutarimu\w*)",  # ruling
    re.I | re.U)


def cadastral_no(text: str) -> str | None:
    """Prefer an explicitly labelled cadastral number over a bare match."""
    m = CAD_LABELLED_RE.search(text or "")
    if m:
        return m.group(1)
    for m in CAD_RE.finditer(text or ""):
        lead = text[max(0, m.start() - 40):m.start()]
        if _CAD_CONTEXT_NOISE_RE.search(lead):
            continue
        return m.group(1)
    return None


def municipality_from(text: str) -> str | None:
    """Municipality in the form config.ALL_MUNICIPALITIES uses, or None.

    A city municipality and a district municipality are two different entries
    in that list — "Kauno miesto" and "Kauno rajono" both exist — so the
    pattern that matched decides the suffix. Formatting every match as
    "X rajono" turned "Vilniaus m. sav." into "Vilniaus rajono": a name that
    is real, confidently wrong, and belongs to somewhere else entirely.

    This is load-bearing beyond display. dedupe.is_duplicate gates on
    municipality by exact match, so a city flat mislabelled as a district
    becomes a merge candidate for a district homestead.

    District first, matching the original precedence: a text carrying both
    forms is far likelier to be a district listing that mentions a city.

    Fix round 2: a regex match is not proof the capture names a real place.
    "Mikrorajonas" (a field label, not a village) satisfies MUNI_RE just
    like a genuine district name would, and gets formatted into "Mikro
    rajono" -- confident, and wrong, because that municipality does not
    exist. The result is validated against ALL_MUNICIPALITIES before it is
    returned; an unrecognised capture comes back None, the same honest
    answer as finding nothing at all, rather than an invented value that
    would silently fail every municipality-gated filter and defeat
    dedupe.is_duplicate's exact-match identity gate against its own twin.
    """
    m = MUNI_RE.search(text or "")
    if m:
        candidate = f"{m.group(1)} rajono"
        return candidate if candidate in ALL_MUNICIPALITIES else None
    m = CITY_RE.search(text or "")
    if m:
        candidate = f"{m.group(1)} miesto"
        return candidate if candidate in ALL_MUNICIPALITIES else None
    return None


def municipality_from_label(value: str | None) -> str | None:
    """A portal's labelled address field mapped to the ALL_MUNICIPALITIES form.

    Some portals expose the municipality as a structured field rather than
    free prose -- rinka.lt renders "Plungės r. sav.", "Vilniaus m. sav.",
    "Rietavo sav.": the address register's own wording, already isolated
    from surrounding text. MUNI_RE/CITY_RE exist to find a place name
    inside a sentence, which is not this job -- the whole value already IS
    the place name plus a fixed suffix, so this maps the suffix directly:
    strip a trailing " sav.", then translate a remaining " r."/" m." to
    " rajono"/" miesto". Multi-word and suffixless municipalities (Kazlų
    Rūdos, Rietavo, Marijampolės, Kalvarijos, Birštono, Neringos, Pagėgių,
    Visagino, Elektrėnų, Druskininkų...) have no " r."/" m." to translate
    and pass through unchanged once " sav." is stripped.

    Validated against ALL_MUNICIPALITIES for the same reason as
    municipality_from: a value the register itself doesn't recognise must
    come back None, not a guess.
    """
    v = (value or "").strip()
    if not v:
        return None
    if v.endswith(" sav."):
        v = v[: -len(" sav.")]
    if v.endswith(" r."):
        v = v[: -len(" r.")] + " rajono"
    elif v.endswith(" m."):
        v = v[: -len(" m.")] + " miesto"
    return v if v in ALL_MUNICIPALITIES else None


def _common(text: str) -> dict[str, Any]:
    plot = _f(PLOT_RE.search(text))
    if plot is None:
        ha = _f(HA_RE.search(text))
        plot = ha * 100 if ha is not None else None
    loc = LOCALITY_RE.search(text)
    return {
        "price_eur": _f(PRICE_RE.search(text)),
        "house_m2": _f(AREA_RE.search(text)),
        "plot_ares": plot,
        "municipality": municipality_from(text),
        "locality": f"{loc.group(1)} k." if loc else None,
        "cadastral_no": cadastral_no(text),
    }


def _first_url(text: str, host_hint: str | None = None) -> str | None:
    urls = URL_RE.findall(text)
    if host_hint:
        for u in urls:
            if host_hint in u:
                return u
    for u in urls:
        if not any(x in u for x in ("unsubscribe", "atsisakyti", "mailto",
                                    "facebook", "twitter", ".png", ".jpg", ".gif")):
            return u
    return urls[0] if urls else None


def _title(text: str) -> str | None:
    for ln in text.splitlines():
        if 12 <= len(ln) <= 180 and not ln.lower().startswith(("http", "sveiki", "gerb")):
            return ln[:180]
    return None


def parse_evarzytynes(text: str) -> dict[str, Any]:
    d = _common(text)
    d["source"] = "evarzytynes"
    d["url"] = _first_url(text, "evarzytynes.lt")
    d["title"] = _title(text)
    # Auction notices carry a start price and an end date.
    m = re.search(r"(?:pradin[ėe]\s*(?:pardavimo\s*)?kaina)\D{0,20}"
                  r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,7})", text, re.I)
    if m:
        d["price_eur"] = _f(m)
    dm = re.search(r"(?:pabaig\w*|iki)\D{0,25}(\d{4}-\d{2}-\d{2})", text, re.I) \
         or DATE_RE.search(text)
    if dm:
        d["auction_ends_at"] = dm.group(1)
    return d


def parse_turtas(text: str) -> dict[str, Any]:
    d = _common(text)
    d["source"] = "turtas"
    d["url"] = _first_url(text, "turtas.lt")
    d["title"] = _title(text)
    m = re.search(r"(?:prad\w+\s*kaina|starto kaina)\D{0,20}"
                  r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,7})", text, re.I)
    if m:
        d["price_eur"] = _f(m)
    return d


def _classified(source: str, host: str) -> Callable[[str], dict[str, Any]]:
    def fn(text: str) -> dict[str, Any]:
        d = _common(text)
        d["source"] = source
        d["url"] = _first_url(text, host)
        d["title"] = _title(text)
        return d
    return fn


def parse_generic(text: str) -> dict[str, Any]:
    d = _common(text)
    d["source"] = "manual"
    d["url"] = _first_url(text)
    d["title"] = _title(text)
    return d


parse_aruodas = _classified("aruodas", "aruodas.lt")
parse_domoplius = _classified("domoplius", "domoplius.lt")
parse_alio = _classified("alio", "alio.lt")
parse_skelbiu = _classified("skelbiu", "skelbiu.lt")
parse_rinka = _classified("rinka", "rinka.lt")
# kampas.lt is a classifieds portal like the four above — one advert per block,
# an asking price, no auction mechanics — so it gets the shared _classified
# extractor and nothing else. It is ALERT_ONLY in registry.py (/robots.txt
# returns 403), and until now it had no parser and no route at all: an alert
# from it would have fallen through to parse_generic and been stored as
# "manual", i.e. as something a human pasted. Nothing about its format calls
# for a bespoke parser; what it will need, like every other portal here, is
# tightening against a real alert once one arrives.
parse_kampas = _classified("kampas", "kampas.lt")

# Declared source key -> parser. ROUTES below is keyed on domain fragments,
# which a caller that already knows the portal ("evarzytynes") cannot match
# against; this is the mapping for that caller. Sniffing the text is a guess,
# and on an auction notice a wrong guess is the difference between the starting
# price and the market valuation — 25000 against 40000 on the same property.
BY_SOURCE: dict[str, Callable[[str], dict[str, Any]]] = {
    "evarzytynes": parse_evarzytynes,
    "turtas": parse_turtas,
    "aruodas": parse_aruodas,
    "domoplius": parse_domoplius,
    "kampas": parse_kampas,
    "alio": parse_alio,
    "skelbiu": parse_skelbiu,
    "rinka": parse_rinka,
}

# Sender-domain / subject fragment -> the source key it names. One table with
# two readers: ROUTES turns it into parsers for the paste path, and
# source_for_alert() answers "whose alert is this?" for the mailbox path.
# Keeping them derived from one list is what stops a portal from being
# routable to a parser while being unnameable as a source, or the reverse.
ALERT_SENDERS: list[tuple[str, str]] = [
    ("evarzytynes.lt", "evarzytynes"),
    ("registrucentras.lt", "evarzytynes"),
    ("turtas.lt", "turtas"),
    ("aruodas.lt", "aruodas"),
    ("domoplius.lt", "domoplius"),
    ("kampas.lt", "kampas"),
    ("alio.lt", "alio"),
    ("skelbiu.lt", "skelbiu"),
    ("rinka.lt", "rinka"),
]

ROUTES: list[tuple[str, Callable[[str], dict[str, Any]]]] = [
    (needle, BY_SOURCE[key]) for needle, key in ALERT_SENDERS
]


def source_for_alert(sender: str, subject: str = "") -> str | None:
    """The portal an alert email's own headers name, or None. PURE.

    Deliberately narrower than route(): it reads the From header (address and
    display name both — portals send through ESPs, so "Aruodas.lt"
    <no-reply@some-esp.net> is normal) and the subject, and never the body.

    The body is the wrong evidence for this question. Every portal's footer
    links to other portals, forwarded digests quote each other, and an
    advertisement can name anyone — so a body sniff answers "which portal is
    mentioned here", not "who sent this". route() may take that guess because
    a human is standing there having pasted the text; an unattended mailbox
    poll may not, because the answer is stored as fact on a candidate row and
    read back as if a portal had asserted it.

    None means "no portal I know", and the caller's honest response to that is
    to leave the message alone, not to store it under some default source.
    """
    hay = f"{sender} {subject}".lower()
    for needle, key in ALERT_SENDERS:
        if needle in hay:
            return key
    return None


def parser_for(source: str | None) -> Callable[[str], dict[str, Any]] | None:
    """The parser a declared source key names, or None if it names nothing.

    None is the honest answer for "manual", "facebook" and anything unknown:
    those have no portal format, so the caller should fall back to sniffing
    rather than being handed an arbitrary parser.
    """
    return BY_SOURCE.get((source or "").strip().lower())


def route(sender: str, subject: str, body: str) -> dict[str, Any]:
    """Pick a parser by sender domain, fall back to subject, then generic."""
    text = to_text(body)
    hay = f"{sender} {subject}".lower()
    for needle, fn in ROUTES:
        if needle in hay:
            return fn(text)
    for needle, fn in ROUTES:
        if needle in text.lower():
            return fn(text)
    return parse_generic(text)


def split_listings(text: str) -> list[str]:
    """A digest email may carry several listings. Split on price markers.

    Conservative: only splits when three or more price markers appear, otherwise
    one email is one listing.
    """
    marks = [m.start() for m in PRICE_RE.finditer(text)]
    if len(marks) < 3:
        return [text]
    bounds = [0] + [max(0, p - 320) for p in marks[1:]] + [len(text)]
    out, seen = [], set()
    for a, b in zip(bounds, bounds[1:]):
        chunk = text[a:b].strip()
        if len(chunk) > 60 and chunk[:80] not in seen:
            seen.add(chunk[:80])
            out.append(chunk)
    return out or [text]
