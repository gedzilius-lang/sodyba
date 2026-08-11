"""The IMAP fetch-parse-store loop, driven by a fake IMAP client.

Until now `poll_mailbox` built its own `imaplib.IMAP4_SSL` inline, so nothing
could run it and not one line of the loop was covered — on the one ingest path
that carries five of the six portals the operator actually cares about, and
that has never executed anywhere. The connection is now injected exactly as
`poller.poll_source` injects its fetcher, and this file exercises the loop
through it.

WHAT THESE TESTS CANNOT PROVE, stated up front so nobody reads more into a
green suite than is there: every alert email below was INVENTED. Getting a
real one means subscribing to the portal and waiting for it, which nobody has
done yet (AGENT.md section 2 says the same about the parsers themselves).
These fixtures are built the way alert mail is really built — multipart
alternative, Lithuanian subject lines in RFC 2047 encoded words, HTML bodies
in quoted-printable and base64, a plaintext part that deliberately carries no
listing so a test that finds one has proved `_body` preferred the HTML — but
being realistic in FORM is not being right in CONTENT. What is demonstrated
here is the loop: routing, the per-run cap, the \\Seen flag, failure handling
and the counts. Whether `parse_aruodas` matches aruodas.lt's true alert layout
is not demonstrated by anything here, and cannot be until a real alert arrives.
"""
import asyncio
import imaplib
import json
import logging
from email.message import EmailMessage

import pytest

from backend.app.db import connect
from backend.app.sources import mailbox, parsers

# Shaped like a Gmail app password (four groups of four) and obviously fake.
# Every test that can leak a credential leaks THIS one.
PASSWORD = "abcd efgh ijkl mnop"

# The plaintext alternative on purpose says nothing about any listing. If
# `_body` ever stopped preferring text/html, every portal test below would
# find no price and fail — which is the point of writing it this way.
PLAIN_ALTERNATIVE = "Šį laišką geriausia skaityti HTML formatu."


def _alert(sender: str, subject: str, html: str, cte: str = "quoted-printable") -> bytes:
    """One multipart/alternative alert email, as bytes off the wire."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "sodyba.alerts@example.net"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 10 Aug 2026 07:14:02 +0300"
    msg.set_content(PLAIN_ALTERNATIVE, subtype="plain", charset="utf-8", cte=cte)
    msg.add_alternative(html, subtype="html", charset="utf-8", cte=cte)
    return msg.as_bytes()


# --------------------------------------------------------------- portal mail
# One invented alert per portal that has a parser and is ALERT_ONLY in the
# registry, plus evarzytynes (auction notice, a different shape). Each names a
# different municipality and village so no two can ever be read as the same
# property by dedupe.

ARUODAS = _alert(
    '"Aruodas.lt" <noreply@aruodas.lt>',
    "Nauji skelbimai pagal Jūsų paiešką — sodybos",
    """<html><body style="font-family:Arial,sans-serif">
<p>Sveiki, Gedai! Pagal Jūsų išsaugotą paiešką „Sodybos Aukštaitijoje“
rasta naujų skelbimų.</p>
<h2>Sodyba prie miško, Utenos r., Kirdeikių k.</h2>
<p>Kaina: 15&nbsp;000 &euro;</p>
<p>Namo plotas: 82 m&sup2; &middot; sklypas 45 arai &middot; statyta 1968 m.</p>
<p>Įvesta elektra, sklype gręžinys ir šulinys, mūrinis tvartas.</p>
<p><a href="https://www.aruodas.lt/sodybos-utenos-r-kirdeikiu-k-4-1234567/">Žiūrėti skelbimą</a></p>
<hr>
<p><a href="https://www.aruodas.lt/paskyra/atsisakyti/?id=98123">Atsisakyti pranešimų</a></p>
</body></html>""")

# Base64 body, and the headline is the link itself — the layout where
# `to_text` glues the href onto the front of the title line. The listing must
# still land; only its stored title suffers, which is why no test below
# asserts a title.
DOMOPLIUS = _alert(
    "Domoplius.lt <info@domoplius.lt>",
    "Naujas skelbimas pagal Jūsų paiešką",
    """<html><body>
<p>Sveiki!</p>
<p><a href="https://www.domoplius.lt/skelbimai/parduodama-sodyba-moletu-r-4855123.html">Sodyba
su pirtimi, Molėtų r., Suginčių k.</a></p>
<p>Kaina 12&nbsp;900 EUR &middot; namas 74 kv. m &middot; sklypas 32 arai</p>
<p>Yra elektra, šulinys, naujas stogas, pirtis prie tvenkinio.</p>
<p><a href="https://www.domoplius.lt/atsisakyti">Atsisakyti</a></p>
</body></html>""",
    cte="base64")

SKELBIU = _alert(
    "Skelbiu.lt paieška <paieska@skelbiu.lt>",
    "Jūsų paieška: 1 naujas skelbimas",
    """<html><body>
<p>Sveiki, Gedai,</p>
<h3>Sodyba Anykščių r., Debeikių k., prie upelio</h3>
<p>Kaina: 9&nbsp;500 &euro;</p>
<p>Namas 68 m&sup2;, sklypas 60 arų, kadastro Nr. 3416/0004:88</p>
<p>Elektra įvesta, šulinys kieme. Reikia remonto.</p>
<p><a href="https://www.skelbiu.lt/skelbimai/sodyba-anyksciu-r-88123456.html">Skelbimas</a></p>
</body></html>""")

ALIO = _alert(
    '"Alio.lt" <robotas@alio.lt>',
    "Naujienos pagal paiešką „sodyba“",
    """<html><body>
<p>Sveiki,</p>
<h3>Parduodama sodyba Zarasų r., Degučių k.</h3>
<p>Kaina: 18&nbsp;500 EUR</p>
<p>Gyvenamasis namas 95 m&sup2;, sklypas 1,2 ha</p>
<p>Yra elektra ir vandentiekis, tvartas, klėtis, sodas.</p>
<p><a href="https://www.alio.lt/skelbimai/89/1234567/parduodama-sodyba-zarasu-r.html">Peržiūrėti</a></p>
</body></html>""",
    cte="base64")

# kampas.lt had no parser and no route at all before this change: its alert
# would have fallen through to parse_generic and been stored as "manual".
KAMPAS = _alert(
    "Kampas.lt <naujienos@kampas.lt>",
    "Nauji skelbimai: sodybos",
    """<html><body>
<p>Sveiki, Gedai!</p>
<h3>Sodyba Ukmergės r., Deltuvos k.</h3>
<p>Kaina 16&nbsp;750 &euro;</p>
<p>Namas 105 m&sup2;, sklypas 25 arai</p>
<p>Elektra, gręžinys, šildymas kietuoju kuru.</p>
<p><a href="https://www.kampas.lt/skelbimai/namai/ukmerges-r-deltuvos-k-1234567">Žiūrėti</a></p>
</body></html>""")

# An auction notice, not a classified: a start price, an end date, and a
# bailiff case number sitting above the real cadastral number.
EVARZYTYNES = _alert(
    "eVaržytynės <pranesimai@evarzytynes.lt>",
    "Naujas turtas pagal Jūsų prenumeratą",
    """<html><body>
<p>Sveiki,</p>
<h3>Varžytynės: gyvenamasis namas su ūkiniais pastatais, Rokiškio r., Obelių k.</h3>
<p>Pradinė pardavimo kaina: 9&nbsp;600 EUR</p>
<p>Namas 78 m&sup2;, sklypas 55 arai. Kadastro Nr. 7320/0003:41</p>
<p>Vykdomoji byla Nr. 0157/2026:12</p>
<p>Varžytynių pabaiga 2026-09-15</p>
<p>Įvesta elektra, šulinys.</p>
<p><a href="https://www.evarzytynes.lt/lt/turtas/12345">Turto skelbimas</a></p>
</body></html>""",
    cte="base64")

# No portal in the registry sends from this address, and the subject names
# none either — but the body links to aruodas.lt, which is exactly the trap:
# a body sniff would file this under "aruodas".
UNKNOWN_SENDER = _alert(
    "Savaitės pasiūlymai <naujienos@pigus-turtas.example.com>",
    "Savaitės pasiūlymai nekilnojamojo turto pirkėjams",
    """<html><body>
<p>Geriausi šios savaitės pasiūlymai!</p>
<h3>Sodyba Utenos r., Kirdeikių k.</h3>
<p>Kaina 14&nbsp;000 EUR, namas 90 m&sup2;, sklypas 30 arų, elektra ir šulinys.</p>
<p>Daugiau: <a href="https://www.aruodas.lt/4-9999999/">aruodas.lt</a></p>
</body></html>""")

# A real alert that happens to have nothing in it. Not a listing, not an
# error — and before this change it was the one shape of message that could
# never be marked seen, because the `if not body` guard skipped straight past
# the store and the same message came back on every run forever.
NOTHING_FOUND = _alert(
    '"Aruodas.lt" <noreply@aruodas.lt>',
    "Jūsų paieškos suvestinė",
    """<html><body>
<p>Sveiki, Gedai!</p>
<p>Pagal Jūsų išsaugotą paiešką naujų skelbimų šiandien nerasta.</p>
<p><a href="https://www.aruodas.lt/paskyra/atsisakyti/?id=98123">Atsisakyti</a></p>
</body></html>""")

EMPTY_BODY = _alert('"Aruodas.lt" <noreply@aruodas.lt>',
                    "Jūsų paieškos suvestinė", "")

# Not an email at all: a truncated download, a disk error, a server bug.
GARBAGE = b"\x00\xff\xfe not a mime message at all \x00"

# A flat in the capital at three times the ceiling — every enabled profile
# rejects it outright ("butas" is in JUNK_WORDS and in auction_hunt's own
# exclusions), so it is a listing that was scanned and rejected, not one that
# was never a listing.
FLAT = _alert(
    '"Aruodas.lt" <noreply@aruodas.lt>',
    "Nauji skelbimai pagal Jūsų paiešką",
    """<html><body>
<p>Sveiki, Gedai!</p>
<h3>Butas Vilniaus m. sav., Žirmūnai</h3>
<p>Kaina: 145&nbsp;000 &euro;, plotas 58 m&sup2;, 3 kambariai</p>
<p><a href="https://www.aruodas.lt/butai-vilniuje-zirmunuose-1-7654321/">Žiūrėti</a></p>
</body></html>""")


def _aruodas_numbered(n: int, muni: str, locality: str, price: int) -> bytes:
    """One more aruodas alert, distinct enough that dedupe keeps it apart.

    The thousands separator is a non-breaking space, written as an escape so
    this file stays pure ASCII outside its Lithuanian strings — the same rule
    parsers.py follows for PRICE_RE, and the same character portals actually
    render.
    """
    return _alert(
        '"Aruodas.lt" <noreply@aruodas.lt>',
        f"Naujas skelbimas: {locality}",
        f"""<html><body>
<p>Sveiki, Gedai!</p>
<h3>Sodyba {muni}, {locality}</h3>
<p>Kaina: {price:,} &euro;</p>
<p>Namas 80 m&sup2;, sklypas 40 arų</p>
<p>Įvesta elektra, yra šulinys.</p>
<p><a href="https://www.aruodas.lt/sodybos-4-{n:07d}/">Žiūrėti skelbimą</a></p>
</body></html>""".replace(",", "\u00a0"))


# ------------------------------------------------------------------- the fake
class FakeIMAP:
    """Stand-in for imaplib.IMAP4_SSL: only the calls poll_mailbox makes.

    It records the protocol side of the run — which ids were fetched, which
    were flagged \\Seen — because that is where two of the behaviours under
    test live and neither is visible in the candidate table.
    """

    def __init__(self, messages, *, login_error=None,
                 raise_on=(), refuse_on=(), null_on=()):
        # Sequence numbers as an IMAP server hands them out: 1-based, oldest
        # first, so the LAST id is the newest message.
        self.messages = {str(i + 1).encode(): raw for i, raw in enumerate(messages)}
        self.login_error = login_error
        self.raise_on = {str(i).encode() for i in raise_on}
        self.refuse_on = {str(i).encode() for i in refuse_on}
        self.null_on = {str(i).encode() for i in null_on}
        self.credentials = None
        self.selected = None
        self.searched = None
        self.fetched: list[bytes] = []
        self.seen: set[bytes] = set()
        self.closed = False
        self.logged_out = False

    def login(self, user, password):
        if self.login_error:
            raise self.login_error
        self.credentials = (user, password)
        return ("OK", [b"LOGIN completed"])

    def select(self, folder):
        self.selected = folder
        return ("OK", [str(len(self.messages)).encode()])

    def search(self, charset, *criteria):
        self.searched = (charset, criteria)
        unseen = [mid for mid in self.messages if mid not in self.seen]
        return ("OK", [b" ".join(unseen)])

    def fetch(self, mid, message_parts):
        self.fetched.append(mid)
        if mid in self.raise_on:
            raise imaplib.IMAP4.abort("socket error: EOF")
        if mid in self.refuse_on:
            return ("NO", [None])
        if mid in self.null_on:
            # An OK response whose literal is NIL. Servers really do this on a
            # message they cannot read back, and it is what turns the payload
            # into None instead of bytes.
            return ("OK", [(b"%s (BODY[] {0}" % mid, None), b")"])
        raw = self.messages[mid]
        return ("OK", [(b"%s (BODY[] {%d}" % (mid, len(raw)), raw), b")"])

    def store(self, mid, command, flags):
        assert command == "+FLAGS", command
        assert "\\Seen" in flags, flags
        self.seen.add(mid)
        return ("OK", [b"1 (FLAGS (\\Seen))"])

    def close(self):
        self.closed = True
        return ("OK", [b"CLOSE completed"])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"logging out"])


@pytest.fixture(autouse=True)
def _imap_env(monkeypatch):
    """config.py reads SR_IMAP_* once at import, so the module globals are
    what a run actually consults — patch those, not the environment."""
    monkeypatch.setattr(mailbox, "IMAP_HOST", "imap.example.net")
    monkeypatch.setattr(mailbox, "IMAP_PORT", 993)
    monkeypatch.setattr(mailbox, "IMAP_USER", "sodyba.alerts@example.net")
    monkeypatch.setattr(mailbox, "IMAP_PASSWORD", PASSWORD)
    monkeypatch.setattr(mailbox, "IMAP_FOLDER", "INBOX")
    monkeypatch.setattr(mailbox, "IMAP_MARK_SEEN", True)
    monkeypatch.setattr(mailbox, "IMAP_MAX_PER_RUN", 40)


@pytest.fixture(autouse=True)
def _empty_table():
    """Every test starts with an empty candidate table: _insert merges against
    whatever else is stored, so rows left by another file would decide whether
    a fixture here is a duplicate. Same reason test_poller.py does it."""
    with connect() as cx:
        cx.execute("DELETE FROM candidate")


def run(fake: FakeIMAP) -> dict:
    return asyncio.run(mailbox.poll_mailbox(imap=lambda: fake))


def rows() -> list[dict]:
    with connect() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT * FROM candidate ORDER BY id").fetchall()]


def last_log() -> dict:
    with connect() as cx:
        return dict(cx.execute(
            "SELECT * FROM refresh_log WHERE source='mailbox' "
            "ORDER BY id DESC LIMIT 1").fetchone())


# ------------------------------------------------------- injection and default
def test_configured_still_means_host_and_user_and_password(monkeypatch):
    assert mailbox.configured() is True
    for blank in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.setattr(mailbox, blank, "")
        assert mailbox.configured() is False, f"{blank} blank must disable the poll"
        monkeypatch.undo()


def test_an_unconfigured_mailbox_is_skipped_not_attempted(monkeypatch):
    monkeypatch.setattr(mailbox, "IMAP_HOST", "")

    def _explode():
        raise AssertionError("no connection may be opened when SR_IMAP_* is blank")

    result = asyncio.run(mailbox.poll_mailbox(imap=_explode))
    assert result == {"status": "skipped", "reason": "IMAP not configured"}


def test_the_default_connection_is_unchanged(monkeypatch):
    """Injection must not have altered what happens when nothing is injected:
    still a TLS connection to SR_IMAP_HOST:SR_IMAP_PORT, still logged in with
    SR_IMAP_USER/SR_IMAP_PASSWORD, still SR_IMAP_FOLDER."""
    fake = FakeIMAP([ARUODAS])
    built = []

    def _ssl(host, port):
        built.append((host, port))
        return fake

    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", _ssl)
    result = asyncio.run(mailbox.poll_mailbox())

    assert built == [("imap.example.net", 993)]
    assert fake.credentials == ("sodyba.alerts@example.net", PASSWORD)
    assert fake.selected == "INBOX"
    assert fake.searched == (None, ("UNSEEN",))
    assert result["status"] == "ok" and len(result["created"]) == 1
    assert fake.closed and fake.logged_out


# ------------------------------------------------------------------- routing
@pytest.mark.parametrize("raw,source,url,price,locality,municipality", [
    (ARUODAS, "aruodas",
     "https://www.aruodas.lt/sodybos-utenos-r-kirdeikiu-k-4-1234567/",
     15000, "Kirdeikių k.", "Utenos rajono"),
    (DOMOPLIUS, "domoplius",
     "https://www.domoplius.lt/skelbimai/parduodama-sodyba-moletu-r-4855123.html",
     12900, "Suginčių k.", "Molėtų rajono"),
    (SKELBIU, "skelbiu",
     "https://www.skelbiu.lt/skelbimai/sodyba-anyksciu-r-88123456.html",
     9500, "Debeikių k.", "Anykščių rajono"),
    (ALIO, "alio",
     "https://www.alio.lt/skelbimai/89/1234567/parduodama-sodyba-zarasu-r.html",
     18500, "Degučių k.", "Zarasų rajono"),
    (KAMPAS, "kampas",
     "https://www.kampas.lt/skelbimai/namai/ukmerges-r-deltuvos-k-1234567",
     16750, "Deltuvos k.", "Ukmergės rajono"),
    (EVARZYTYNES, "evarzytynes",
     "https://www.evarzytynes.lt/lt/turtas/12345",
     9600, "Obelių k.", "Rokiškio rajono"),
])
def test_an_alert_from_each_portal_becomes_a_candidate(
        raw, source, url, price, locality, municipality):
    """The whole path, per portal: quoted-printable or base64 multipart in,
    a stored candidate out, attributed to the portal whose address sent it."""
    fake = FakeIMAP([raw])
    result = run(fake)

    assert result["status"] == "ok"
    assert result["scanned"] == 1 and result["rejected"] == 0
    assert len(result["created"]) == 1, result

    stored = rows()
    assert len(stored) == 1
    row = stored[0]
    assert row["source"] == source
    assert row["url"] == url
    assert row["price_eur"] == price
    assert row["locality"] == locality, "Lithuanian text must survive the decode"
    assert row["municipality"] == municipality
    assert row["match_state"] == "match"
    assert json.loads(row["profiles_json"]), "a match must record which profile it hit"


def test_the_auction_notice_keeps_its_start_price_and_end_date():
    """parse_evarzytynes overrides the generic price with the START price and
    reads the auction deadline — the two fields that make an auction lot a
    different thing from a classified advert."""
    run(FakeIMAP([EVARZYTYNES]))
    row = rows()[0]
    assert row["price_eur"] == 9600
    assert row["auction_ends_at"] == "2026-09-15"
    assert row["cadastral_no"] == "7320/0003:41", \
        "the bailiff case number must not be taken for the cadastral number"
    assert "auction_hunt" in json.loads(row["profiles_json"])


def test_a_message_from_no_known_portal_is_not_stored_under_a_guessed_source():
    """The body links to aruodas.lt and carries a plausible price, area and
    village — everything a parser needs. The sender is nobody we know, so it
    is left alone: filing it under "aruodas" would put a claim on a stored row
    that no portal ever made, and "manual" would say a human pasted it."""
    fake = FakeIMAP([UNKNOWN_SENDER])
    result = run(fake)

    assert result["status"] == "ok"
    assert result["unknown"] == 1
    assert result["scanned"] == 0 and result["rejected"] == 0
    assert result["created"] == []
    assert rows() == [], "nothing may be stored for a sender we cannot name"
    assert fake.seen == {b"1"}, "a handled message is consumed, not re-read forever"


def test_the_sender_decides_the_source_not_the_body():
    """Stated directly at the unit that decides it, so the reason the test
    above passes cannot drift into "it happened not to parse"."""
    assert parsers.source_for_alert("naujienos@pigus-turtas.example.com",
                                    "Savaitės pasiūlymai") is None
    assert parsers.source_for_alert('"Aruodas.lt" <no-reply@esp-mailer.net>',
                                    "Nauji skelbimai") == "aruodas"
    assert parsers.source_for_alert("pranesimai@evarzytynes.lt", "") == "evarzytynes"


# ---------------------------------------------------------------- the run cap
def test_the_run_cap_takes_the_newest_messages(monkeypatch):
    """A backlog bigger than SR_IMAP_MAX_PER_RUN must be worked from the new
    end. The oldest alerts describe listings most likely already sold; taking
    the head would leave the newest ones permanently behind the backlog."""
    monkeypatch.setattr(mailbox, "IMAP_MAX_PER_RUN", 2)
    fake = FakeIMAP([
        _aruodas_numbered(1, "Utenos r.", "Kirdeikių k.", 11000),
        _aruodas_numbered(2, "Molėtų r.", "Suginčių k.", 12000),
        _aruodas_numbered(3, "Anykščių r.", "Debeikių k.", 13000),
        _aruodas_numbered(4, "Zarasų r.", "Degučių k.", 14000),
        _aruodas_numbered(5, "Ignalinos r.", "Mielagėnų k.", 15000),
    ])
    result = run(fake)

    assert fake.fetched == [b"4", b"5"], "the newest two, not the oldest two"
    assert result["scanned"] == 2
    assert {r["price_eur"] for r in rows()} == {14000, 15000}
    assert fake.seen == {b"4", b"5"}


def test_the_backlog_is_worked_through_over_successive_runs(monkeypatch):
    monkeypatch.setattr(mailbox, "IMAP_MAX_PER_RUN", 2)
    fake = FakeIMAP([
        _aruodas_numbered(1, "Utenos r.", "Kirdeikių k.", 11000),
        _aruodas_numbered(2, "Molėtų r.", "Suginčių k.", 12000),
        _aruodas_numbered(3, "Anykščių r.", "Debeikių k.", 13000),
    ])
    run(fake)
    run(fake)
    assert fake.fetched == [b"2", b"3", b"1"]
    assert {r["price_eur"] for r in rows()} == {11000, 12000, 13000}


# ------------------------------------------------------------------ mark seen
def test_mark_seen_true_consumes_every_handled_message():
    fake = FakeIMAP([ARUODAS, NOTHING_FOUND, UNKNOWN_SENDER])
    run(fake)
    assert fake.seen == {b"1", b"2", b"3"}, \
        "a match, an empty digest and an unknown sender were all handled"


def test_mark_seen_false_consumes_nothing(monkeypatch):
    """SR_IMAP_MARK_SEEN=false is the operator's safety net while he is still
    checking that ingestion works. It has to mean the mailbox is untouched."""
    monkeypatch.setattr(mailbox, "IMAP_MARK_SEEN", False)
    fake = FakeIMAP([ARUODAS, NOTHING_FOUND])
    run(fake)
    assert fake.seen == set()
    assert len(rows()) == 1, "the listing is still ingested; only the flag is withheld"

    # ...and the proof it was not consumed: the same messages come back.
    run(fake)
    assert fake.fetched == [b"1", b"2", b"1", b"2"]


def test_a_second_poll_of_the_same_alerts_creates_no_new_rows(monkeypatch):
    """AGENT.md's ingest table claims this and nothing exercised it end to end:
    with the \\Seen flag withheld, the identical alerts arrive again and the
    fingerprint check has to absorb them."""
    monkeypatch.setattr(mailbox, "IMAP_MARK_SEEN", False)
    fake = FakeIMAP([ARUODAS, DOMOPLIUS, KAMPAS])
    first = run(fake)
    second = run(fake)

    assert len(first["created"]) == 3
    assert second["created"] == [], "a re-sent alert must not be pushed again"
    assert second["scanned"] == 3, "it was still read and judged"
    assert len(rows()) == 3


# --------------------------------------------------------- malformed messages
def test_a_message_that_is_not_an_email_at_all_is_survivable():
    """Bytes that parse into no headers and no body. `email` does not raise on
    these — it hands back a Message with defects — so what saves us is that a
    message naming no sender is nobody's alert and is never parsed by guess."""
    fake = FakeIMAP([ARUODAS, GARBAGE, KAMPAS])
    result = run(fake)

    assert result["status"] == "ok"
    assert result["unknown"] == 1
    assert {r["source"] for r in rows()} == {"aruodas", "kampas"}
    assert result["scanned"] == 2


def test_a_message_that_raises_on_the_way_through_does_not_abort_the_run():
    """A NIL body: the payload is None, so parsing it raises. One message must
    not cost the run the messages after it — nor the rows already written,
    which is what returning {"status": "error"} from the middle of the loop
    used to do."""
    fake = FakeIMAP([ARUODAS, DOMOPLIUS, KAMPAS], null_on=[2])
    result = run(fake)

    assert result["status"] == "ok"
    assert result["failed"] == 1
    assert {r["source"] for r in rows()} == {"aruodas", "kampas"}
    assert fake.seen == {b"1", b"3"}, "the message that broke us stays as evidence"


def test_an_empty_body_stores_nothing_and_is_still_consumed():
    fake = FakeIMAP([EMPTY_BODY])
    result = run(fake)
    assert result["scanned"] == 0 and result["created"] == []
    assert rows() == []
    assert fake.seen == {b"1"}, \
        "an empty body is a handled message, not an unfinished one"


def test_a_digest_with_no_listing_in_it_stores_no_half_parsed_row():
    fake = FakeIMAP([NOTHING_FOUND])
    result = run(fake)
    assert result["scanned"] == 0 and result["rejected"] == 0
    assert rows() == []


# ------------------------------------------------------------------- failures
def test_a_refused_login_is_returned_not_raised():
    fake = FakeIMAP([ARUODAS], login_error=imaplib.IMAP4.error(
        "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"))
    result = run(fake)

    assert result["status"] == "error"
    assert "AUTHENTICATIONFAILED" in result["error"]
    assert fake.fetched == [] and rows() == []
    assert last_log()["status"] == "error"


def test_a_refused_login_cannot_leak_the_password(caplog):
    """imaplib quotes the LOGIN command back inside several of its own error
    messages, and this error text goes three places: the JSON response
    /api/ingest/mailbox returns, a toast in the browser, and the refresh_log
    row that the console prints on every page load. None of them may ever
    hold the credential."""
    caplog.set_level(logging.DEBUG)
    fake = FakeIMAP([ARUODAS], login_error=imaplib.IMAP4.error(
        f'command: LOGIN => LOGIN sodyba.alerts@example.net "{PASSWORD}" '
        f'failed: [AUTHENTICATIONFAILED]'))
    result = run(fake)

    assert PASSWORD not in result["error"]
    assert mailbox.REDACTED in result["error"]
    assert PASSWORD not in last_log()["detail"]
    assert PASSWORD not in caplog.text


def test_a_backslash_quoted_password_is_redacted_too(monkeypatch, caplog):
    """imaplib escapes backslashes and quotes before it sends the password, so
    the form that comes back in an error is not always the literal one."""
    caplog.set_level(logging.DEBUG)
    awkward = 'pa\\ss"word'
    monkeypatch.setattr(mailbox, "IMAP_PASSWORD", awkward)
    quoted = awkward.replace("\\", "\\\\").replace('"', '\\"')
    fake = FakeIMAP([ARUODAS],
                    login_error=imaplib.IMAP4.error(f'LOGIN "{quoted}" refused'))
    result = run(fake)

    assert awkward not in result["error"] and quoted not in result["error"]
    assert mailbox.REDACTED in result["error"]


def test_a_blank_password_does_not_turn_redaction_into_nonsense(monkeypatch):
    """str.replace("", x) inserts x between every character. configured()
    stops a blank password reaching a run, but _redact is the wrong place to
    depend on that."""
    monkeypatch.setattr(mailbox, "IMAP_PASSWORD", "")
    assert mailbox._redact("Invalid credentials") == "Invalid credentials"


def test_a_mid_run_fetch_failure_is_reported_and_costs_only_its_own_message():
    """The connection drops on the second message. The first is already
    stored, the third must still be read, and the failure has to appear in
    the return value rather than being swallowed."""
    fake = FakeIMAP([ARUODAS, DOMOPLIUS, KAMPAS], raise_on=[2])
    result = run(fake)

    assert result["status"] == "ok"
    assert result["failed"] == 1
    assert {r["source"] for r in rows()} == {"aruodas", "kampas"}
    assert fake.seen == {b"1", b"3"}, \
        "the message that failed must stay unseen so the next run retries it"


def test_a_fetch_the_server_refuses_is_left_for_the_next_run():
    fake = FakeIMAP([ARUODAS, DOMOPLIUS], refuse_on=[1])
    result = run(fake)

    assert result["failed"] == 1
    assert [r["source"] for r in rows()] == ["domoplius"]
    assert fake.seen == {b"2"}


def test_a_failure_hanging_up_does_not_discard_the_run():
    """close()/logout() happen after every row is committed. Reporting the
    run as an error there would throw away a created list that has already
    been written to the database — and with it the Telegram push."""
    fake = FakeIMAP([ARUODAS])

    def _boom():
        raise imaplib.IMAP4.abort("BYE Connection closed")

    fake.logout = _boom
    result = run(fake)

    assert result["status"] == "ok"
    assert len(result["created"]) == 1
    assert len(rows()) == 1


# --------------------------------------------------------------------- counts
def test_the_summary_counts_describe_what_actually_happened():
    """Four messages: one listing that matches, one listing every profile
    rejects, one digest with no listing in it, one sender we cannot name.

    `scanned` counts listings judged, not messages and not the banner blocks
    inside them — the same meaning poller.py gives the word, since the two
    numbers appear side by side in the ingest log.
    """
    fake = FakeIMAP([ARUODAS, FLAT, NOTHING_FOUND, UNKNOWN_SENDER])
    result = run(fake)

    assert result["scanned"] == 2, "the sodyba and the flat; the digest had none"
    assert result["rejected"] == 1, "the flat"
    assert len(result["created"]) == 1, "the sodyba"
    assert result["unknown"] == 1
    assert result["failed"] == 0
    assert len(rows()) == 1

    detail = last_log()["detail"]
    assert "1 nauji" in detail and "peržiūrėta 2" in detail
    assert "atmesta pagal filtrus 1" in detail
    assert "nežinomų siuntėjų 1" in detail


def test_the_created_entry_carries_what_the_push_needs():
    """notify.push formats straight off this dict; a missing key there is a
    notification that does not say what it is about."""
    result = run(FakeIMAP([ARUODAS]))
    entry = result["created"][0]
    for key in ("ref", "profiles", "title", "municipality", "locality",
                "price_eur", "house_m2", "plot_ares", "url", "source"):
        assert key in entry, f"push payload is missing {key}"
    assert entry["source"] == "aruodas"
    assert entry["price_eur"] == 15000
    assert entry["profiles"], "a created row must name the profile it matched"
