"""Source-text checks over the "Tikrinti dabar" ingestion control.

Same constraint as test_frontend_responsive.py and test_frontend_match_state.py:
there is no JS runtime in this suite (no node, no jsdom, no build step —
AGENT.md section 3), so app.js cannot be executed here. These assert over
source text only. They prove the handler is wired to call both ingestion
routes, that a skipped mailbox is not folded into the same "bad" path as a
real error, and that the status line reads the schema fields it is supposed
to. They cannot prove the toast reads well, that the button actually stays
disabled for the ~100s a real poll takes, or that the phone card layout looks
right — those were checked by reading the rendered page and by curling the
three endpoints against the running server.

Context: before this change, #btnPoll only called POST /api/ingest/mailbox.
On the production deployment SR_IMAP_* is deliberately blank (no alert
mailbox exists), so the button always reported "skipped" and never touched
POST /api/ingest/poll — the only ingestion path that works there. checkNow()
replaces pollMailbox() to call both.

Two of the four minimum assertions the task description asks for do not
apply here and are noted rather than faked: no new table row/cell was added,
so there is nothing new for a data-label to drift out of sync with (the
existing test_frontend_responsive.py coverage over #candTable/#mktTable is
unchanged and still passes); and no new interactive control was added — the
button is the same #btnPoll, already sized by the generic `.btn` rule under
`@media (pointer:coarse)`. test_existing_touch_target_coverage_still_applies_
to_btn_poll below re-affirms that coverage rather than skipping it silently.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

CSS = re.sub(r"\s*([{};:,])\s*", r"\1", re.sub(r"\s+", " ", STYLES))


def _fn(name: str) -> str:
    """Source of one `function name(...) { ... }` or `async function name...`,
    matched by brace-depth so nested braces inside the body don't truncate it."""
    m = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", APP_JS)
    assert m, f"no function named {name!r} found in app.js"
    start = m.end()
    depth = 1
    i = start
    while depth:
        if APP_JS[i] == "{":
            depth += 1
        elif APP_JS[i] == "}":
            depth -= 1
        i += 1
    return APP_JS[start:i]


# ------------------------------------------------------- both paths, honestly
def test_pollmailbox_is_gone_the_handler_was_renamed():
    """pollMailbox no longer describes what the button does (it only hit
    /ingest/mailbox); the name must not linger as a stale reference."""
    assert "pollMailbox" not in APP_JS


def test_check_now_calls_the_source_poll_endpoint():
    body = _fn("checkNow")
    assert "runSourcePoll" in body


def test_check_now_calls_the_mailbox_endpoint():
    body = _fn("checkNow")
    assert "runMailboxPoll" in body


def test_run_source_poll_posts_to_ingest_poll():
    assert "api('/ingest/poll', { method: 'POST' })" in _fn("runSourcePoll")


def test_run_mailbox_poll_posts_to_ingest_mailbox():
    assert "api('/ingest/mailbox', { method: 'POST' })" in _fn("runMailboxPoll")


def test_button_click_is_wired_to_check_now():
    assert "$('btnPoll').onclick = checkNow;" in APP_JS


# ------------------------------------------------ skipped mailbox != error
def test_skipped_mailbox_gets_its_own_branch_before_the_error_branch():
    """summariseMailbox must check status === 'skipped' ahead of (and
    distinctly from) status === 'error', or a skipped mailbox would fall
    through into the error message."""
    body = _fn("summariseMailbox")
    skipped_at = body.index("'skipped'")
    error_at = body.index("'error'")
    assert skipped_at < error_at


def test_skipped_mailbox_does_not_set_the_bad_flag():
    """Only an actual error may mark the toast red; skipped is an intended
    state on a deployment with no configured mailbox."""
    body = _fn("runMailboxPoll")
    assert "result.status === 'error'" in body
    assert "'skipped'" not in body  # skipped-ness is decided inside summariseMailbox, not here


def test_summarise_mailbox_skipped_text_says_not_configured_not_failed():
    body = _fn("summariseMailbox")
    skipped_branch = body.split("'skipped'", 1)[1].split("\n", 1)[0]
    assert "klaida" not in skipped_branch  # "error" in Lithuanian must not appear here


def test_source_poll_bad_flag_is_derived_per_source_status():
    """A source-level result is bad if any source's status isn't 'ok' —
    poll_all() never returns 'skipped' (every polled source is configured by
    definition; registry.py gates that before polling starts), so this only
    needs to separate ok from error."""
    body = _fn("runSourcePoll")
    assert "r.status !== 'ok'" in body


# --------------------------------------------------------------- the button
def test_button_disables_while_working_and_restores_its_label():
    body = _fn("checkNow")
    assert "btn.disabled = true" in body
    assert "btn.disabled = false" in body
    assert "'Tikrinti dabar'" in body


def test_button_label_names_which_phase_is_running():
    """A poll can take a minute or more; a static 'Tikrinama…' does not tell
    the user which of the two requests is still in flight."""
    body = _fn("checkNow")
    assert "šaltiniai" in body.lower()
    assert "paštas" in body.lower()


def test_check_now_refreshes_candidates_and_status_after_both_requests():
    body = _fn("checkNow")
    assert "await loadCandidates();" in body
    assert "await loadIngestStatus();" in body
    # both refreshes must come after both network calls, not interleaved
    assert body.index("runMailboxPoll") < body.index("loadCandidates")


# --------------------------------------------------------- the status line
def test_status_line_reads_polled_sources_from_schema():
    body = _fn("loadIngestStatus")
    assert "ing.polled" in body


def test_status_line_reads_stale_sources_from_schema():
    body = _fn("loadIngestStatus")
    assert "ing.stale_sources" in body


def test_status_line_only_shows_stale_warning_when_non_empty():
    body = _fn("loadIngestStatus")
    guarded = body.split("if (stale.length)", 1)
    assert len(guarded) == 2, "stale-sources warning must be conditional on a non-empty list"
    assert "stat-warn" in guarded[1]


def test_every_value_written_into_the_status_line_passes_through_esc():
    """loadIngestStatus assigns innerHTML (to embed the warning span), so
    every server-derived value it interpolates — mail fields and source
    names alike — must go through esc(), matching the habit everywhere else
    in this file."""
    body = _fn("loadIngestStatus")
    for needle in ("esc(mail.status)", "esc(mail.detail)", "esc(mail.ended_at)",
                   "esc(byKey[k]?.host || k)"):
        assert needle in body, f"{needle!r} missing — an unescaped value would reach innerHTML"
    # esc(byKey...) must appear twice: once for the polled list, once for stale
    assert body.count("esc(byKey[k]?.host || k)") == 2


def test_status_line_still_reports_whether_the_mailbox_is_configured():
    """Pre-existing behaviour (mailbox_configured drives the fallback text
    when there is no log entry yet) must survive the rewrite."""
    assert "ing.mailbox_configured" in _fn("loadIngestStatus")


# ------------------------------------------------------------------- CSS
def test_stale_warning_has_a_distinct_colour():
    assert ".stat-warn{color:var(--ochre)}" in CSS


def test_ingest_bar_wraps_on_phones_so_the_status_line_sits_above_the_button():
    block = CSS.split("@media (max-width:700px)", 1)[1].split("@media (max-width:620px)", 1)[0]
    assert ".ingest-bar{flex-wrap:wrap}" in block
    assert ".ingest-bar .btn{flex:1 1 auto}" in block


def test_existing_touch_target_coverage_still_applies_to_btn_poll():
    """No new interactive control was added — #btnPoll is unchanged in
    index.html and stays a `.btn`, so the existing pointer:coarse rule still
    sizes it; this pins that the rule (and the button's class) are both
    still there rather than silently assuming it."""
    assert re.search(r'id="btnPoll"[^>]*class="btn', INDEX_HTML) or \
        re.search(r'class="btn[^"]*"[^>]*id="btnPoll"', INDEX_HTML)
    coarse_block = CSS.split("@media (pointer:coarse)", 1)[1].split("}", 1)[0]
    assert ".btn{" in coarse_block or ".btn," in coarse_block


def test_button_label_in_html_is_unchanged_lithuanian():
    """The constraint is that the control now does something honest, not
    that its label changes — "Tikrinti dabar" ("check now") already
    describes what a user wants, regardless of which endpoints answer it."""
    assert '>Tikrinti dabar<' in INDEX_HTML
