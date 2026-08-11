# Turning on email-alert ingestion

Six portals hold nearly all the property in your price band, and none of them
may be crawled (`backend/app/sources/registry.py` records each verdict and
`poller.py` enforces it). Their own alert emails are the lawful route in. Point
those alerts at one mailbox and `sources/mailbox.py` turns them into scored
candidates every 15 minutes.

Until you do this, nothing arrives on its own.

---

## 1. Make a dedicated mailbox

Use a fresh account, not your personal one. The poller reads every unseen
message in the folder and flags what it has handled.

Gmail:

1. Create the account, e.g. `sodyba.alerts@gmail.com`.
2. Turn on 2-step verification (app passwords need it).
3. Generate an **App Password** at <https://myaccount.google.com/apppasswords>.
   Gmail shows 16 characters in four groups. **The account password will not
   work over IMAP** — Google refuses plain-password IMAP logins outright.
4. Settings → *Forwarding and POP/IMAP* → enable IMAP.

## 2. Fill `.env`

Copy `.env.example` to `.env` and set these. `.env` is gitignored and must
stay that way; this file, and every other file in the repository, must never
contain the real value.

| Variable | Put in it |
|---|---|
| `SR_IMAP_HOST` | `imap.gmail.com` (or your provider's IMAP host) |
| `SR_IMAP_PORT` | `993` — TLS. There is no plaintext mode. |
| `SR_IMAP_USER` | the full address, e.g. `sodyba.alerts@gmail.com` |
| `SR_IMAP_PASSWORD` | the app password, e.g. `xxxx-xxxx-xxxx-xxxx`. **A live credential.** |
| `SR_IMAP_FOLDER` | `INBOX`, or the label you filter alerts into |
| `SR_IMAP_MAX_PER_RUN` | `40`. A bigger backlog is worked newest-first, over successive runs. |
| `SR_IMAP_MARK_SEEN` | `false` for the first day, then `true` (see §5) |
| `SR_MAILBOX_POLL_MINUTES` | `15` |

All three of `SR_IMAP_HOST`, `SR_IMAP_USER` and `SR_IMAP_PASSWORD` must be
non-empty or the poll is skipped and the scheduler never registers the job.

Treat the password as live: keep it in `.env` only, never paste it into a
commit, an issue, a screenshot or a support thread, and rotate it at the link
above if it ever leaves the box. The app redacts it from every error it
returns or logs (`mailbox._redact`), but that is a backstop, not permission.

## 3. Subscribe on each portal

Create a saved search on each site, switch its **email alert** on, and address
it to the mailbox above. Set the alert to arrive as soon as there is something
new, not as a weekly digest.

Save the search **wide** — this app does the narrowing, and a near miss it
would have shown you is worth more than a listing an over-tight portal filter
threw away:

- Category: **sodybos / gyvenamieji namai** (not butai, not sklypai).
- Price: **3 000 – 25 000 EUR**.
- Geography: **whole country**. Do not set a floor on floor area or plot size.

The Žemaitija corner (Rietavas, Plungė, Plateliai, Platelių ežeras, Lūkstas)
needs no separate alert — the `zemaitija_lakes` profile already searches 15 km
around those five centres and scores anything that lands there. Add a second
saved search restricted to **Rietavo sav., Plungės r., Telšių r., Šilalės r.**
only if you later narrow the nationwide one.

Where to click:

| Portal | Control |
|---|---|
| aruodas.lt | search results → *Išsaugoti paiešką* → alerts on |
| domoplius.lt | search results → *Gauti pranešimus* |
| skelbiu.lt | search results → *Prenumeruoti paiešką* |
| alio.lt | search results → *Prenumerata* |
| kampas.lt | search results → *Išsaugoti paiešką* |
| evarzytynes.lt | account → *Prenumerata*: nekilnojamasis turtas, gyvenamosios paskirties |

Optional and worth having: the Turto bankas *Pardavimai ir nuoma* newsletter
(turtas.lt), which is understood too.

## 4. Restart and verify

Restart the app — the mailbox job is registered at boot, from `.env`, and only
if the three variables above are set. Then:

```bash
curl -X POST http://127.0.0.1:8000/api/ingest/mailbox
```

- `{"status":"skipped"}` — the variables did not reach the process.
- `{"status":"error", ...}` — connection or login. The text is safe to paste;
  the password is stripped out of it.
- `{"status":"ok","created":[...],"scanned":N,"rejected":N,"unknown":N,"failed":N}`

The console's status line prints the same run in Lithuanian —
`N nauji; peržiūrėta S; atmesta pagal filtrus R`, plus
`nežinomų siuntėjų U` and `neperskaityta laiškų F` when either is not zero.

- **peržiūrėta** — listings judged (not messages, not banner blocks).
- **atmesta pagal filtrus** — listings no enabled profile wanted.
- **nežinomų siuntėjų** — messages from an address naming no portal we know.
  Anything above zero means a portal is mailing from a domain nobody added
  yet: add it to `ALERT_SENDERS` in `backend/app/sources/parsers.py`.
- **neperskaityta laiškų** — messages that could not be read. They are left
  unread and retried on the next run.

## 5. Which portals are understood on arrival

Recognised, with a parser each: **aruodas.lt, domoplius.lt, kampas.lt,
skelbiu.lt, alio.lt, evarzytynes.lt** (and `registrucentras.lt`, turtas.lt,
rinka.lt).

**Everything else is not understood and is not stored.** The portal is decided
by the message's `From` header, never by what the body links to — a stored
row's source is read back as fact, and every portal's footer links to the
others. A message from an unrecognised sender is counted as *nežinomų
siuntėjų*, marked read, and left in the mailbox.

Two consequences to plan around:

- **Do not hand-forward alerts** from another account. Manual forwarding
  rewrites `From` to you, and the message is then unrecognised. Use a
  server-side auto-forwarding rule (Gmail: Settings → *Filters* → *Forward
  it to*), which keeps the original sender.
- Set `SR_IMAP_MARK_SEEN=false` for the first day. Nothing is consumed, so you
  can re-run the poll as often as you like while checking that alerts land and
  parse. Switch it to `true` once you are satisfied, or every run will keep
  re-reading the whole mailbox.

## 6. Expect the first real alerts to be imperfect

No parser here has ever seen a genuine alert email — they were written against
what the portals publish on the web, and the loop is tested against invented
messages. Expect a field or two missing on the first ones (a price on an
auction notice, a village name, a title that came through as a price).

When the first real alert from each portal lands, save the raw message before
touching any regex — Gmail: ⋮ → *Show original* → *Download Original* — and
tighten `parsers.py` against that actual text. Do not rewrite the parsers
speculatively; the raw messages are the only evidence that matters.
