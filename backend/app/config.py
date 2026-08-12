"""Runtime configuration. Everything overridable by environment variable."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.getenv("SR_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sodyba.db"

FRONTEND_DIR = Path(os.getenv("SR_FRONTEND_DIR", BASE_DIR / "frontend"))

# data.gov.lt Spinta API. robots.txt: Allow: / — automated access permitted.
DATA_GOV_BASE = "https://get.data.gov.lt/"
HTTP_TIMEOUT = float(os.getenv("SR_HTTP_TIMEOUT", "120"))
HTTP_UA = os.getenv("SR_USER_AGENT", "sodyba-radar/1.0 (private research; contact via VPS admin)")

# Politeness: seconds between successive upstream calls.
REQUEST_DELAY = float(os.getenv("SR_REQUEST_DELAY", "1.0"))

# Market-context refresh. The building register changes slowly; daily is generous.
REFRESH_CRON_HOUR = int(os.getenv("SR_REFRESH_HOUR", "4"))
REFRESH_ON_BOOT = os.getenv("SR_REFRESH_ON_BOOT", "true").lower() == "true"

# NTR single-family residential building purpose code.
PASK_TIPAS_VIENBUTIS = 110

# All 60 Lithuanian municipalities — leave the watchlist empty to scan everything.
ALL_MUNICIPALITIES = [
    "Akmenės rajono", "Alytaus miesto", "Alytaus rajono", "Anykščių rajono",
    "Birštono", "Biržų rajono", "Druskininkų", "Elektrėnų", "Ignalinos rajono",
    "Jonavos rajono", "Joniškio rajono", "Jurbarko rajono", "Kaišiadorių rajono",
    "Kalvarijos", "Kauno miesto", "Kauno rajono", "Kazlų Rūdos", "Kelmės rajono",
    "Klaipėdos miesto", "Klaipėdos rajono", "Kretingos rajono", "Kupiškio rajono",
    "Kėdainių rajono", "Lazdijų rajono", "Marijampolės", "Mažeikių rajono",
    "Molėtų rajono", "Neringos", "Pagėgių", "Pakruojo rajono", "Palangos miesto",
    "Panevėžio miesto", "Panevėžio rajono", "Pasvalio rajono", "Plungės rajono",
    "Prienų rajono", "Radviliškio rajono", "Raseinių rajono", "Rietavo",
    "Rokiškio rajono", "Skuodo rajono", "Tauragės rajono", "Telšių rajono",
    "Trakų rajono", "Ukmergės rajono", "Utenos rajono", "Varėnos rajono",
    "Vilkaviškio rajono", "Vilniaus miesto", "Vilniaus rajono", "Visagino",
    "Zarasų rajono", "Šakių rajono", "Šalčininkų rajono", "Šiaulių miesto",
    "Šiaulių rajono", "Šilalės rajono", "Šilutės rajono", "Širvintų rajono",
    "Švenčionių rajono",
]

DEFAULT_MUNICIPALITIES = [
    "Ukmergės rajono", "Utenos rajono", "Anykščių rajono", "Molėtų rajono",
    "Rokiškio rajono", "Zarasų rajono", "Ignalinos rajono", "Švenčionių rajono",
    "Varėnos rajono", "Lazdijų rajono", "Šalčininkų rajono", "Trakų rajono",
    "Alytaus rajono", "Prienų rajono", "Kupiškio rajono", "Biržų rajono",
    "Kelmės rajono", "Širvintų rajono", "Vilniaus rajono",
]

# ---------------------------------------------------------------- mailbox
# The portals we may not crawl all offer their own filtered email alerts.
# Point those at a dedicated mailbox and the poller ingests them automatically.
IMAP_HOST = os.getenv("SR_IMAP_HOST", "")
IMAP_PORT = int(os.getenv("SR_IMAP_PORT", "993"))
IMAP_USER = os.getenv("SR_IMAP_USER", "")
IMAP_PASSWORD = os.getenv("SR_IMAP_PASSWORD", "")
IMAP_FOLDER = os.getenv("SR_IMAP_FOLDER", "INBOX")
IMAP_MAX_PER_RUN = int(os.getenv("SR_IMAP_MAX_PER_RUN", "40"))
IMAP_MARK_SEEN = os.getenv("SR_IMAP_MARK_SEEN", "true").lower() == "true"
MAILBOX_POLL_MINUTES = int(os.getenv("SR_MAILBOX_POLL_MINUTES", "15"))

# ---------------------------------------------------------------- poller
# Only sources declared POLL in sources/registry.py are ever fetched.
POLL_MINUTES = int(os.getenv("SR_POLL_MINUTES", "60"))
POLL_MAX_PER_RUN = int(os.getenv("SR_POLL_MAX_PER_RUN", "40"))
# A category is walked page by page until it yields nothing new. The cap stops
# a pathological or looping response from spinning forever, and a run that hits
# it says so rather than looking complete.
#
# It is a safety net, not an operating limit: 5 pages x per_page=200 is 1000
# listings, against a largest real category of 372. If it ever fires, something
# upstream is wrong. A capped run also cannot advance its cursor (the pages it
# never reached hold lower ids -- see poller._poll_category), so it makes no
# forward progress until SR_POLL_MAX_PAGES is raised. That stall is deliberate,
# and the run says so in its log line rather than failing quietly.
POLL_MAX_PAGES = int(os.getenv("SR_POLL_MAX_PAGES", "5"))

# ---------------------------------------------------------------- notify
TELEGRAM_TOKEN = os.getenv("SR_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("SR_TELEGRAM_CHAT_ID", "")
