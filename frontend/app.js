/* Sodyba Radar dashboard.
   No framework, no build step. State lives on the server; this file renders it. */

const $ = (id) => document.getElementById(id);
// Base-relative, not `/api...`, so the app works mounted under a path prefix
// (a reverse proxy stripping /sodyba) as well as at the root. Relies on the
// page being loaded at a directory URL: serve /sodyba/, not /sodyba.
const api = async (path, opts = {}) => {
  const r = await fetch(`api${path}`, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  });
  if (r.status === 204) return null;
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
};

// Ten bands for the core-sample column. Cool at the top of the stack (site and
// utilities), warm at the bottom (money and risk).
const BAND = ['#8fae5d', '#7d9e63', '#6b8f79', '#5c7a8a', '#4f6f92',
              '#6a6f95', '#87698d', '#a86a76', '#c8983a', '#a94f3c'];

const VERDICT_LT = {
  shortlist: 'Trumpasis sąrašas', weak: 'Per silpnas', over_budget: 'Virš biudžeto',
  incomplete: 'Neįvertintas', rejected: 'Atmestas',
};

const MATCH_LT = { match: 'Atitinka', near: 'Beveik' };

let SCHEMA = null;      // criteria, flags, cost lines, checks, municipalities
let SETTINGS = null;    // weights, budget, min score, contingency
let CURRENT = null;     // candidate open in the drawer, or null for a new one
let TIMER = null;
let PROFILES = [];      // saved search profiles the ingestion bot applies
let EDITING = null;     // profile key open in the editor

const fmt = (n, dp = 0) =>
  n === null || n === undefined ? '—'
    : Number(n).toLocaleString('lt-LT', { minimumFractionDigits: dp, maximumFractionDigits: dp });

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (m) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m]));

const KM = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${m} m`);

function toast(msg, bad = false) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.toggle('is-bad', bad);
  t.hidden = false;
  clearTimeout(t._t);
  t._t = setTimeout(() => { t.hidden = true; }, 3600);
}

/* ------------------------------------------------------------ core sample */
function coreBar(cand) {
  const el = document.createElement('div');
  el.className = 'core';
  const scores = cand.scores || {};
  const w = SETTINGS.weights;
  const filled = SCHEMA.criteria.filter((c) => typeof scores[c.key] === 'number').length;
  if (!filled) {
    el.classList.add('is-void');
    el.textContent = 'NEĮVERTINTA';
    return el;
  }
  const total = SCHEMA.criteria.reduce((s, c) => s + (w[c.key] ?? c.default_weight), 0) || 1;
  SCHEMA.criteria.forEach((c, i) => {
    const seg = document.createElement('i');
    const weight = (w[c.key] ?? c.default_weight) / total;
    const score = scores[c.key];
    seg.style.width = `${weight * 100}%`;
    seg.style.background = BAND[i];
    seg.style.opacity = typeof score === 'number' ? (0.13 + 0.87 * (score / 10)) : 0.06;
    seg.title = `${c.label}: ${typeof score === 'number' ? score : '—'}/10 · svoris ${(weight * 100).toFixed(0)}%`;
    el.appendChild(seg);
  });
  return el;
}

function natureCell(c) {
  const n = c.nature || {};
  if (!n.located) return '<span class="nat w-none">—</span>';
  const bits = [];
  if (n.nearest_lake) {
    bits.push(`<span class="w-lake">◈ ${esc(n.nearest_lake.name)} ` +
      `<b>${KM(n.nearest_lake.distance_m)}</b> · ${n.nearest_lake.size.toFixed(0)} ha</span>`);
  }
  if (n.nearest_river) {
    bits.push(`<span class="w-river">≈ ${esc(n.nearest_river.name)} ` +
      `<b>${KM(n.nearest_river.distance_m)}</b></span>`);
  }
  if ((n.protected_areas || []).length) {
    bits.push(`<span class="w-prot">▲ saugoma (${n.protected_areas.length})</span>`);
  }
  return `<div class="nat">${bits.join('<br>') || '—'}</div>`;
}

/* Score and the ranking metric in one cell.
   These were two columns, "Balas" and "EUR/tšk.", and on an unscored candidate
   both printed an em dash — two full columns of dashes, because unscored is the
   normal state until the scores are entered by hand. Stating the same absence
   twice is noise, and it cost the judgement columns room they needed on the
   right, where losing the verdict is the worst outcome.
   One column now. Scored: the weighted score with EUR per score point beneath
   it — the actual ranking metric (AGENT.md section 8), so it stays visible
   rather than being demoted to a tooltip. Unscored: a single dash. The row
   still says "unscored" in words — the core-sample column prints NEĮVERTINTA
   and the verdict tag reads "Neįvertintas" — so nothing is hidden by the dash;
   it is a placeholder, not the message. No number is invented either way. */
function scoreCell(c) {
  if (c.weighted_score === null || c.weighted_score === undefined) {
    return '<span class="muted">—</span>';
  }
  const per = c.eur_per_point === null || c.eur_per_point === undefined
    ? '' : `<small class="per">${fmt(c.eur_per_point)} EUR/tšk.</small>`;
  return `${c.weighted_score.toFixed(2)}${per}`;
}

/* ------------------------------------------------ price, its age, its peers
   Three facts about one number, in one cell, because the table is full: nine
   columns, and clipping VERDIKTAS off the right edge was the last bug fixed
   here. How long a price has been asked and how it compares with the other
   adverts are both statements *about the price*, so they belong with it.
   Hierarchy carries the reading order — the asked figure at full size, the two
   context lines at 10.5px and dimmed — so the column still scans as a column
   of prices and the context is there when a row is worth a second look. */

/* Days on the market, in the largest unit that stays honest. "1 863 d." is a
   number the reader has to divide before it means anything; "5,1 m." is the
   fact itself. Days are kept for the short end, where they are the fact.
   Years are floored, not rounded, and divided by DAYS_PER_YEAR — the same
   divisor STALE_DAYS is expressed in. That coupling is the point: rounding to
   nearest printed "5,0 m." on 1 816 days and on 1 837 days, one of them
   accented and one not, and a colour that contradicts the number printed
   beside it is worse than no colour. Flooring on a shared divisor makes
   "reads 5,0 m. or more" and "is accented" the same statement. */
const DAYS_PER_YEAR = 365;

function ageText(days) {
  if (days < 90) return `${days} d.`;
  if (days < 730) return `${Math.round(days / 30.44)} mėn.`;
  const years = Math.floor((days / DAYS_PER_YEAR) * 10) / 10;
  return `${years.toFixed(1).replace('.', ',')} m.`;
}

/* Five years. An advert nobody has bought in five years is the strongest
   evidence this app can get that the asking price is wrong — and unlike a
   valuation it costs one subtraction.
   The threshold is set high on purpose. The median advert in the collected set
   has been running about 3.6 years, so a two- or three-year rule would put the
   accent on three rows in four and the accent would stop meaning anything.
   The age is printed on every row either way; only the colour is rationed.
   Five DAYS_PER_YEAR years exactly, so that the accent switches on at the same
   instant the printed figure reaches 5,0 m. — see ageText. */
const STALE_DAYS = 5 * DAYS_PER_YEAR;

/* Which unit each ratio compares, spelled out on screen: a ratio against the
   price per are of land and one against the price per m² of house are
   different claims, and a column that silently switched between them could not
   be read down. EUR/a is preferred because 18 of the 28 real listings carry no
   floor area at all; EUR/m² is the fallback, never the silent substitute. */
const PEER_UNIT = { eur_per_are: 'EUR/a', eur_per_m2: 'EUR/m²' };
const PEER_BASIS = { municipality: 'sav.', all: 'visi' };

function peerFigure(c) {
  const p = c.asking_vs_peers || {};
  for (const metric of ['eur_per_are', 'eur_per_m2']) {
    const f = p[metric];
    if (f && f.ratio !== null && f.ratio !== undefined) return [f, metric];
  }
  return [null, null];                 // absence is the normal case, not an error
}

// 0,12× · 5,86× · 623×. Two decimals stop being informative once a ratio is in
// the double digits, and at that point the interesting fact is the order of
// magnitude — usually a plot size the portal mistyped.
const ratioText = (r) =>
  (Math.abs(r) >= 10 ? `${Math.round(r)}×` : `${Number(r).toFixed(2).replace('.', ',')}×`);

/* The long form, for the cell's title. Says asked-price, says median, says how
   many and on what basis, and denies being a valuation in as many words. */
function peerTitle(f, metric) {
  const unit = PEER_UNIT[metric];
  const where = f.basis === 'municipality'
    ? 'tos pačios savivaldybės skelbimų' : 'visų surinktų skelbimų';
  return `Prašoma ${fmt(f.value, 2)} ${unit} · ${where} prašomų kainų mediana ` +
    `${fmt(f.median, 2)} ${unit} (n=${fmt(f.n)}). Lyginamos PRAŠOMOS kainos — ` +
    `tai nėra turto vertinimas ir ne rinkos vertė.`;
}

function priceCell(c) {
  const bits = [`<b class="ask">${fmt(c.price_eur)}</b>`];

  if (typeof c.days_listed === 'number') {
    const stale = c.days_listed >= STALE_DAYS;
    const title = `Paskelbta ${c.listed_at || '?'} · ${fmt(c.days_listed)} d.` +
      (stale ? ' · skelbiama ilgiau nei penkerius metus' : '');
    bits.push(`<small class="ctx age${stale ? ' is-stale' : ''}" title="${esc(title)}">` +
      `skelbiama ${ageText(c.days_listed)}</small>`);
  }

  // Never bare: a ratio without its sample size and basis is a number wearing
  // more authority than it has. n=12 and n=29 are different claims and the
  // cell says which one this is.
  const [f, metric] = peerFigure(c);
  if (f) {
    bits.push(`<small class="ctx peer" title="${esc(peerTitle(f, metric))}">` +
      `${ratioText(f.ratio)} prašomų med.` +
      `<span class="basis">${PEER_UNIT[metric]} · n=${fmt(f.n)} · ` +
      `${esc(PEER_BASIS[f.basis] || f.basis)}</span></small>`);
  }
  return `<div class="pricecell">${bits.join('')}</div>`;
}

/* A merged-duplicate line looks like:
   "[dublikatas rinka] · 17300 EUR · 81 m2 · 41 a · <title> · <url>"
   appended to notes when dedupe folds a second-source listing into this row.
   Surface it as a small chip — otherwise it is only visible by already
   suspecting a merge and opening the row's notes. */
function duplicateChip(notes) {
  if (!notes || !notes.includes('[dublikatas')) return '';
  const lines = notes.split('\n').filter((l) => l.includes('[dublikatas'));
  return `<span class="chip chip-dup" title="${esc(lines.join('\n'))}">dublikatas</span>`;
}

/* ------------------------------------------------------------- candidates */
function filterQuery() {
  const p = new URLSearchParams();
  const put = (k, v) => { if (v !== '' && v !== null && v !== undefined) p.set(k, v); };
  put('q', $('fQuery').value.trim());
  put('min_price', $('fMinPrice').value);
  put('max_price', $('fMaxPrice').value);
  put('municipality', $('fMuni').value);
  put('profile', $('fProfile').value);
  if ($('fNear').value.trim() && $('fRadius').value) {
    p.set('near', $('fNear').value.trim());
    p.set('radius_km', $('fRadius').value);
  }
  if ($('fLake').value) p.set('max_lake_m', Number($('fLake').value) * 1000);
  if ($('fRiver').value) p.set('max_river_m', Number($('fRiver').value) * 1000);
  put('verdict', $('fVerdict').value);
  put('min_score', $('fMinScore').value);
  put('max_total_cost', $('fMaxCost').value);
  put('sort', $('fSort').value);
  if ($('fArchived').checked) p.set('include_archived', 'true');
  put('match_state', $('fMatchState').value);
  return p.toString();
}

/* How many filters are currently narrowing the list.
   The definition is the app's own: exactly the controls "Išvalyti filtrus"
   resets, counting a non-empty value as one filter, with "netoli" + "spindulys"
   counting once because neither does anything without the other. Sort order and
   "rodyti archyvuotus" are deliberately not counted — they reorder and widen the
   list, they never hide a candidate.
   This number exists because the panel collapses on a handset: a filter that is
   quietly removing rows while its control is off-screen is the failure mode
   worth guarding against. */
const FILTER_IDS = ['fQuery', 'fMinPrice', 'fMaxPrice', 'fMuni', 'fProfile',
                    'fVerdict', 'fMinScore', 'fMaxCost', 'fLake', 'fRiver'];

function activeFilterCount() {
  const n = FILTER_IDS.filter((id) => String($(id).value).trim() !== '').length;
  return n + ($('fNear').value.trim() && $('fRadius').value ? 1 : 0);
}

function updateFilterCount() {
  const n = activeFilterCount();
  const el = $('filterCount');
  el.textContent = n === 0 ? 'nėra' : (n === 1 ? 'aktyvus: 1' : `aktyvūs: ${n}`);
  el.classList.toggle('is-on', n > 0);
}

async function loadCandidates() {
  const data = await api(`/candidates?${filterQuery()}`);
  const body = $('candBody');
  body.textContent = '';
  $('candCount').textContent = `${data.count} objekt${data.count === 1 ? 'as' : 'ai'}`;
  $('candEmpty').hidden = data.count > 0;

  // The server's own words for what the ×med. figure is and is not. textContent,
  // not innerHTML, and never a second wording maintained here.
  $('peersNote').textContent = data.asking_vs_peers_note || '';

  updateFilterCount();

  data.items.forEach((c) => {
    const tr = document.createElement('tr');
    if (c.archived) tr.classList.add('is-archived');
    if (c.match_state === 'near') tr.classList.add('is-near');
    tr.onclick = () => openDrawer(c);

    // The third argument is the column's header text. Below 700px the table
    // becomes one card per row and the <thead> is hidden, so each cell has to
    // carry its own label — styles.css prints it from data-label. Keep these
    // identical to the <th> in index.html; test_frontend_responsive.py fails
    // if they drift, because a card labelled differently from the table it
    // replaces is worse than no label at all.
    const td = (html, cls, label) => {
      const d = document.createElement('td');
      if (cls) d.className = cls;
      if (label) d.dataset.label = label;
      if (html instanceof Node) d.appendChild(html); else d.innerHTML = html;
      return d;
    };
    tr.appendChild(td(`<span class="ref">${esc(c.ref)}</span>`, null, 'Nr.'));
    const chips = (c.profiles || [])
      .map((k) => `<span class="chip">${esc(profileName(k))}</span>`).join('') + duplicateChip(c.notes);
    // Where it is and where it came from. A row with no source says so: an
    // empty half of "municipality · source" reads as a rendering gap, and the
    // gap is exactly what made a missing source invisible in the first place.
    const provenance = [c.municipality, c.source || 'šaltinis nenurodytas']
      .filter(Boolean).map(esc).join(' · ');
    tr.appendChild(td(
      `<span class="place">${esc(c.locality || c.title || '—')}` +
      `<small>${provenance}</small>${chips}</span>`,
      null, 'Vietovė'));
    tr.appendChild(td(priceCell(c), 'num', 'Kaina'));
    tr.appendChild(td(coreBar(c), null, 'Vertinimo pjūvis'));
    tr.appendChild(td(scoreCell(c), 'num', 'Balas'));
    tr.appendChild(td(fmt(c.total_cost), 'num', 'Visi kaštai'));
    tr.appendChild(td(natureCell(c), null, 'Gamta'));
    const misses = Object.values(c.misses || {}).flat();
    const why = misses.map((m) => m.text).join(' · ');
    tr.appendChild(td(
      `<span class="tag ${c.match_state}" title="${esc(why)}">` +
      `${MATCH_LT[c.match_state] || c.match_state}</span>` +
      (c.match_state === 'near' && why
        ? `<small class="miss">${esc(why)}</small>` : ''),
      null, 'Atitikimas'));
    tr.appendChild(td(
      `<span class="tag ${c.verdict}" title="${esc(c.verdict_reason)}">${VERDICT_LT[c.verdict]}</span>`,
      null, 'Verdiktas'));
    body.appendChild(tr);
  });

  renderProfiles(profileCounts(data.items));

  const t = $('tallies');
  t.textContent = '';
  Object.entries(data.by_verdict).forEach(([k, n]) => {
    const s = document.createElement('span');
    s.className = `tally ${k}`;
    s.innerHTML = `<b>${n}</b>${VERDICT_LT[k]}`;
    t.appendChild(s);
  });
}

/* ----------------------------------------------------------------- market */
async function loadMarket() {
  const data = await api('/market');
  const body = $('mktBody');
  body.textContent = '';
  data.items.forEach((m) => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'default';
    // data-label mirrors this table's <th>; see the note on the candidate rows.
    tr.innerHTML =
      `<td data-label="Savivaldybė">${esc(m.municipality)}</td>` +
      `<td class="num" data-label="Vienbučiai">${fmt(m.total)}</td>` +
      `<td class="num" data-label="Elektra+vanduo">${fmt(m.power_and_water)}</td>` +
      `<td class="num" data-label="% el.+vand.">${(m.pct_power_water * 100).toFixed(1)}%</td>` +
      `<td class="num" data-label="% iki 1945">${(m.pct_pre_1945 * 100).toFixed(1)}%</td>` +
      `<td class="num" data-label="Rąstiniai">${fmt(m.log_walls)}</td>` +
      `<td class="num" data-label="Retumo indeksas">${m.rarity_index.toFixed(2)}</td>`;
    body.appendChild(tr);
  });
  const when = data.last_refresh && data.last_refresh.ended_at;
  $('marketNote').textContent = data.items.length
    ? `Registrų centras · atnaujinta ${when || '—'}`
    : 'Duomenų dar nėra — paspausk „Atnaujinti registro duomenis“';
  $('refreshStatus').textContent = data.last_refresh
    ? `Paskutinis atnaujinimas: ${data.last_refresh.ended_at} (${data.last_refresh.status})`
    : 'Registro duomenys dar neatsiųsti';
}

/* ---------------------------------------------------------------- weights */
function renderWeights() {
  const box = $('weights');
  box.textContent = '';
  SCHEMA.criteria.forEach((c, i) => {
    const row = document.createElement('div');
    row.className = 'wrow';
    const pct = Math.round((SETTINGS.weights[c.key] ?? c.default_weight) * 100);
    row.innerHTML =
      `<label><span class="wswatch" style="background:${BAND[i]}"></span>${esc(c.label)}</label>` +
      `<input type="number" min="0" max="100" step="1" value="${pct}" data-w="${c.key}">`;
    box.appendChild(row);
  });
  box.oninput = (e) => {
    const key = e.target.dataset.w;
    if (!key) return;
    SETTINGS.weights[key] = Math.max(0, Number(e.target.value) || 0) / 100;
    updateWeightSum();
    clearTimeout(TIMER);
    TIMER = setTimeout(saveWeights, 450);
  };
  updateWeightSum();
}

function updateWeightSum() {
  const sum = SCHEMA.criteria.reduce((s, c) => s + (SETTINGS.weights[c.key] ?? 0), 0);
  const el = $('weightSum');
  el.textContent = `${Math.round(sum * 100)}%`;
  el.style.color = Math.abs(sum - 1) < 0.005 ? 'var(--paper)' : 'var(--ochre)';
  el.title = Math.abs(sum - 1) < 0.005 ? '' : 'Svoriai normalizuojami automatiškai';
}

async function saveWeights() {
  SETTINGS = await api('/settings', {
    method: 'PUT', body: JSON.stringify({ weights: SETTINGS.weights }),
  });
  await loadCandidates();
}

/* --------------------------------------------------------------- profiles */
const profileName = (key) =>
  (PROFILES.find((p) => p.key === key) || {}).name || key;

function renderProfiles(counts = {}) {
  const box = $('profiles');
  box.textContent = '';
  PROFILES.forEach((p) => {
    const row = document.createElement('div');
    row.className = `prow${p.enabled ? '' : ' is-off'}`;
    // The checkbox sits in a <label> of its own so a thumb has something 44px
    // to hit: .prow is a div, so unlike the flag and check rows there is no
    // label wrapping the whole row for the tap to land on.
    row.innerHTML =
      `<label class="ptoggle"><input type="checkbox" ${p.enabled ? 'checked' : ''} ` +
      `title="Įjungti profilį"></label>` +
      `<span class="pname">${esc(p.name)}<small>${esc(p.note || '')}</small></span>` +
      `<span class="pcount">${counts[p.key] ?? 0}</span>` +
      `<button class="pedit" title="Redaguoti">redag.</button>`;
    row.querySelector('input').onchange = async (e) => {
      p.enabled = e.target.checked;
      row.classList.toggle('is-off', !p.enabled);
      await saveProfiles();
    };
    row.querySelector('.pedit').onclick = () => {
      openDrawer(null);
      document.querySelector('[data-tab="profile"]').click();
      $('epPick').value = p.key;
      loadProfileEditor();
    };
    box.appendChild(row);
  });

  const opts = PROFILES
    .map((p) => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('');

  const sel = $('fProfile');
  const keep = sel.value;
  sel.innerHTML = '<option value="">Visi profiliai</option>' + opts;
  sel.value = keep;
  // A profile that is gone cannot stay in the filter: `.value` would read ""
  // and the search would quietly stop filtering while the box showed nothing
  // at all. Say "Visi profiliai", which is what is then actually happening.
  if (sel.selectedIndex < 0) sel.selectedIndex = 0;

  // The editor's picker is rebuilt from the same list rather than only at
  // boot. It used to be built once, so renaming a profile and saving left the
  // picker labelling it with the name it no longer had — watched happening in
  // Chrome — while the filter beside it had already updated.
  const pick = $('epPick');
  const editing = pick.value;
  pick.innerHTML = opts;
  pick.value = editing;
  if (pick.selectedIndex < 0 && pick.options.length) {
    // The profile being edited no longer exists. Move to a real one and load
    // it, so the fields below are never another profile's values under this
    // one's name.
    pick.selectedIndex = 0;
    if (EDITING) loadProfileEditor();
  }
}

async function saveProfiles() {
  const r = await api('/profiles', {
    method: 'PUT', body: JSON.stringify({ profiles: PROFILES }),
  });
  PROFILES = r.profiles;
  await loadCandidates();
}

function profileCounts(items) {
  const c = {};
  items.forEach((x) => (x.profiles || []).forEach((k) => { c[k] = (c[k] || 0) + 1; }));
  return c;
}

function loadProfileEditor() {
  const p = PROFILES.find((x) => x.key === $('epPick').value);
  if (!p) return;
  EDITING = p.key;
  $('epName').value = p.name || '';
  $('epEnabled').checked = !!p.enabled;
  $('epMinPrice').value = p.min_price ?? '';
  $('epMaxPrice').value = p.max_price ?? '';
  $('epMinPlot').value = p.min_plot_ares ?? '';
  $('epMinArea').value = p.min_house_m2 ?? '';
  $('epMunis').value = (p.municipalities || []).join(', ');
  $('epAny').value = (p.require_any || []).join(', ');
  $('epAll').value = (p.require_all || []).join(', ');
  $('epNot').value = (p.exclude_any || []).join(', ');
  $('epSources').value = (p.sources || []).join(', ');
  $('epCentres').value = (p.centres || []).join(', ');
  $('epRadius').value = p.radius_km ?? '';
  $('epLake').value = p.max_lake_m ?? '';
  $('epRiver').value = p.max_river_m ?? '';
  $('epLakeHa').value = p.min_lake_ha ?? '';
  renderCentreResolution();
}

/* Read the centres back as the gazetteer understood them. Two failures hide
   here and only one of them is caught elsewhere: a centre that resolves to
   nothing becomes a hard miss at evaluation time (filters._radius_misses), but
   a centre that resolves to the WRONG place looks exactly like a correct one —
   "Varniai" resolves to Varnionių k. in Radviliškio rajono, 150 km from the
   Varniai anyone means, and the profile would search there without a word.
   The centres box is one comma-separated line, so a municipality cannot be
   typed per centre; echoing the answer is the cheapest thing that makes a
   wrong one visible. */
async function renderCentreResolution() {
  const box = $('epCentresOut');
  const centres = csv($('epCentres').value);
  if (!centres.length) { box.textContent = ''; return; }
  let r;
  try {
    r = await api('/centres/resolve', {
      method: 'POST', body: JSON.stringify({ centres }),
    });
  } catch (e) {
    box.textContent = `Centrų patikrinti nepavyko: ${e.message}`;
    return;
  }
  box.innerHTML = r.centres.map((c) => {
    const d = c.resolved;
    if (!d) return `<b>${esc(c.centre)} → nerasta</b>`;
    const ha = d.size_ha ? ` ${Math.round(d.size_ha)} ha` : '';
    const where = d.kind === 'lake' ? `ežeras${ha}` : (d.municipality || '');
    return `${esc(c.centre)} → ${esc(d.name)}${where ? ` (${esc(where)})` : ''}`;
  }).join(' · ');
}

const csv = (v) => v.split(',').map((x) => x.trim()).filter(Boolean);
const numOrNull = (v) => (v === '' ? null : Number(v));

async function saveProfileEditor() {
  const i = PROFILES.findIndex((x) => x.key === EDITING);
  if (i < 0) return toast('Pirma pasirink profilį', true);
  PROFILES[i] = {
    ...PROFILES[i],
    name: $('epName').value || PROFILES[i].name,
    enabled: $('epEnabled').checked,
    min_price: numOrNull($('epMinPrice').value),
    max_price: numOrNull($('epMaxPrice').value),
    min_plot_ares: numOrNull($('epMinPlot').value),
    min_house_m2: numOrNull($('epMinArea').value),
    municipalities: csv($('epMunis').value),
    require_any: csv($('epAny').value),
    require_all: csv($('epAll').value),
    exclude_any: csv($('epNot').value),
    sources: csv($('epSources').value),
    centres: csv($('epCentres').value),
    radius_km: numOrNull($('epRadius').value),
    max_lake_m: numOrNull($('epLake').value),
    max_river_m: numOrNull($('epRiver').value),
    min_lake_ha: numOrNull($('epLakeHa').value),
  };
  await saveProfiles();
  toast('Profilis išsaugotas');
}

async function testProfile() {
  const text = $('epTest').value.trim();
  if (!text) return toast('Įklijuok testinį tekstą', true);
  const r = await api('/profiles/test', { method: 'POST', body: JSON.stringify({ text }) });
  const p = r.parsed;
  const n = p.nature || {};
  const out = $('epTestOut');
  out.innerHTML =
    `<div class="testrow"><b>Atpažinta</b><span>` +
    `${esc(p.municipality || '?')} · ${p.price_eur ? fmt(p.price_eur) + ' EUR' : 'kaina ?'}` +
    ` · ${p.house_m2 ?? '?'} m² · ${p.plot_ares ?? '?'} a</span></div>` +
    (n.located && n.nearest_lake
      ? `<div class="testrow"><b>Gamta</b><span>ežeras ${esc(n.nearest_lake.name)} ` +
        `${KM(n.nearest_lake.distance_m)}</span></div>` : '') +
    r.results.map((x) =>
      `<div class="testrow ${x.matched ? 'ok' : 'no'}"><b>${esc(x.name)}</b>` +
      `<span>${x.matched ? 'PRAEINA' : esc(x.reason)}</span></div>`).join('');
}

/* ----------------------------------------------------------------- advice */
function renderNature(n) {
  const box = $('natureBox');
  if (!n || !n.located) {
    box.innerHTML = `<div class="natcard"><h4>Vieta</h4>` +
      `<div class="muted">${esc((n && n.note) || 'Dar nenustatyta.')}</div></div>`;
    return;
  }
  const rows = [];
  if (n.matched_place) rows.push(['Atitikta gyvenvietė', n.matched_place]);
  rows.push(['LKS-94', `${n.easting.toFixed(0)} / ${n.northing.toFixed(0)}`]);
  if (n.nearest_lake) {
    rows.push(['Artimiausias ežeras',
      `${n.nearest_lake.name} · ${KM(n.nearest_lake.distance_m)} · ${n.nearest_lake.size.toFixed(0)} ha`]);
  }
  if (n.nearest_river) {
    rows.push(['Artimiausia upė',
      `${n.nearest_river.name} · ${KM(n.nearest_river.distance_m)} · ${n.nearest_river.size.toFixed(0)} km`]);
  }
  (n.protected_areas || []).slice(0, 5).forEach((p) =>
    rows.push(['Saugoma teritorija', `${p.name} (${p.kind})`]));
  rows.push(['Išvesti balai',
    `vanduo ${n.derived_scores.water} · miškas/vanduo ${n.derived_scores.forest_water}`]);

  box.innerHTML = `<div class="natcard"><h4>Gamta ir vanduo</h4>` +
    rows.map(([a, b]) => `<div class="natrow"><span>${esc(a)}</span><span>${esc(b)}</span></div>`).join('') +
    `<p class="hint" style="margin:10px 0 0">${esc(n.note || '')}</p></div>`;
}

async function locate() {
  if (!CURRENT || !CURRENT.id) return toast('Pirma išsaugok objektą', true);
  const b = $('btnLocate');
  b.disabled = true; b.textContent = 'Matuojama…';
  try {
    const c = await api(`/candidates/${CURRENT.id}/locate`, { method: 'POST' });
    CURRENT = structuredClone(c);
    renderNature(c.nature);
    renderScores();
    await loadCandidates();
    toast(c.nature.located ? 'Vieta nustatyta' : 'Vietos nustatyti nepavyko');
  } catch (e) { toast(e.message, true); }
  b.disabled = false; b.textContent = 'Nustatyti vietą ir išmatuoti gamtą';
}

async function loadAdvice() {
  if (!CURRENT || !CURRENT.id) return toast('Pirma išsaugok objektą', true);
  const r = await api(`/candidates/${CURRENT.id}/advice`);
  const cls = { 'Netinka': 'no', 'Verta apžiūros': 'ok' }[r.stance] || 'hold';
  $('adviceBox').innerHTML =
    `<span class="stance ${cls}">${esc(r.stance)}</span>` +
    r.findings.map((f) =>
      `<div class="find ${f.weight}"><em>${esc(f.topic)}</em>${esc(f.text)}</div>`).join('') +
    r.blockers.map((b) => `<div class="block">${esc(b)}</div>`).join('') +
    r.actions.map((a) => `<div class="act">${esc(a)}</div>`).join('');
}

/* -------------------------------------------------------------- ingestion */
// POST /api/ingest/poll answers {source_key: {status, created, scanned, rejected}}
// — one entry per key in sources/poller.py's POLLED list. status is "ok" or
// "error"; there is no "skipped" at this level because every polled source is,
// by definition, configured (registry.py gates that at start-up, not per-call).
function summarisePoll(result) {
  const keys = Object.keys(result || {});
  if (!keys.length) return 'šaltinių nėra';
  return keys.map((k) => {
    const r = result[k] || {};
    if (r.status !== 'ok') return `${k}: klaida`;
    const n = (r.created || []).length;
    return n
      ? `${k}: ${n} nauji (peržiūrėta ${r.scanned ?? 0})`
      : `${k}: naujų nėra (peržiūrėta ${r.scanned ?? 0})`;
  }).join(', ');
}

// POST /api/ingest/mailbox answers {status: "ok", created, scanned, rejected}
// or {status: "skipped", reason} when SR_IMAP_* is blank, or {status: "error",
// error}. "skipped" is an intended state on a deployment with no alert
// mailbox — it must never be reported the same way as "error".
function summariseMailbox(r) {
  if (r.status === 'skipped') return 'paštas: neįjungtas';
  if (r.status === 'error') return r.error ? `paštas: klaida (${r.error})` : 'paštas: klaida';
  const n = (r.created || []).length;
  return n ? `paštas: ${n} nauji` : `paštas: naujų nėra`;
}

async function runSourcePoll() {
  try {
    const result = await api('/ingest/poll', { method: 'POST' });
    return { text: summarisePoll(result), bad: Object.values(result).some((r) => r.status !== 'ok') };
  } catch (e) {
    return { text: `šaltiniai: klaida (${e.message})`, bad: true };
  }
}

async function runMailboxPoll() {
  try {
    const result = await api('/ingest/mailbox', { method: 'POST' });
    return { text: summariseMailbox(result), bad: result.status === 'error' };
  } catch (e) {
    return { text: `paštas: klaida (${e.message})`, bad: true };
  }
}

// The only ingestion control in the console. It used to hit /ingest/mailbox
// alone, which on a deployment with no alert mailbox (SR_IMAP_* blank) meant
// pressing it did nothing. It now runs both ingestion paths — the rinka.lt
// poller and the mailbox — and reports each honestly, because a control that
// looks like it works while doing nothing is worse than no control at all.
// A poll can take a minute or more (crawl-delay between listings), so the
// button stays disabled and its label names whichever phase is running.
async function checkNow() {
  const btn = $('btnPoll');
  btn.disabled = true;
  btn.textContent = 'Tikrinami šaltiniai…';
  const poll = await runSourcePoll();
  btn.textContent = 'Tikrinamas paštas…';
  const mailbox = await runMailboxPoll();
  toast(`${poll.text} · ${mailbox.text}`, poll.bad || mailbox.bad);
  await loadCandidates();
  await loadIngestStatus();
  btn.disabled = false;
  btn.textContent = 'Tikrinti dabar';
}

async function loadIngestStatus() {
  try {
    const r = await api('/ingest/log?limit=5');
    const ing = SCHEMA.ingest;
    const byKey = Object.fromEntries((ing.sources || []).map((s) => [s.key, s]));
    const mail = r.items.find((x) => x.source === 'mailbox');
    const mailText = mail
      ? `paštas: ${esc(mail.status)} · ${esc(mail.detail)} · ${esc(mail.ended_at)}`
      : (ing.mailbox_configured
          ? 'paštas sukonfigūruotas, dar netikrinta'
          : 'paštas neįjungtas — žr. README');
    const polled = (ing.polled || []).map((k) => esc(byKey[k]?.host || k));
    const sourcesText = polled.length
      ? `tikrinama: ${polled.join(', ')}`
      : 'automatiškai tikrinamų šaltinių nėra';
    let html = `${sourcesText} · ${mailText}`;
    // A source absent here for over 90 days means its robots.txt permission
    // has not been re-checked since — the thing that keeps polling lawful.
    const stale = (ing.stale_sources || []).map((k) => esc(byKey[k]?.host || k));
    if (stale.length) {
      html += ` · <span class="stat-warn">⚠ robots.txt patikra senesnė nei 90 d.: ${stale.join(', ')}</span>`;
    }
    $('ingestStatus').innerHTML = html;
  } catch { /* status line is cosmetic */ }
}

/* ----------------------------------------------------------------- drawer */

/* Seller contacts. Read-only — they arrive with the advert and api.py erases
   them on archive — and rendered as links because on a phone that is the whole
   point: one tap dials, one tap writes.
   A row is rendered only for a contact that exists. None of the 28 real
   rinka.lt listings carries an email, so a fixed "El. paštas —" row would
   print an empty label on every candidate in the table; the email row appears
   the day a source supplies one and not before. */
function renderContacts(c) {
  const box = $('dContacts');
  const rows = [];
  if (c.contact_phone) {
    // The href gets digits and a leading + only; the row shows whatever the
    // portal wrote. A tel: URI carrying a stray character is a dead link, and
    // the value is third-party text either way.
    const dial = String(c.contact_phone).replace(/[^\d+]/g, '');
    rows.push(['Telefonas',
      `<a href="tel:${esc(dial)}">${esc(c.contact_phone)}</a>`]);
  }
  if (c.contact_email) {
    rows.push(['El. paštas',
      `<a href="mailto:${esc(c.contact_email)}">${esc(c.contact_email)}</a>`]);
  }
  // Nothing at all on an unsaved form is not a finding — there is no advert
  // yet. On a stored candidate it is one, so it is stated.
  if (!rows.length && !c.id) { box.textContent = ''; return; }
  box.innerHTML = '<div class="natcard"><h4>Kontaktai</h4>' +
    (rows.length
      ? rows.map(([k, v]) => `<div class="natrow"><span>${k}</span><span>${v}</span></div>`).join('')
      : '<div class="natrow"><span class="muted">Kontaktų nėra</span></div>') +
    '</div>';
}

/* The advert itself, one tap away, beside the URL it opens.
   Only http(s) reaches the href — a pasted `javascript:` URL would otherwise
   turn this button into a one-click script injection into the console, and the
   URL is third-party text. Anything else, including no URL at all (pasted
   candidates often have none), hides the button rather than leaving a dead
   one: an <a> without href is not a link, but it still looks like a button. */
function syncOpenAd(url) {
  const a = $('btnOpenAd');
  const ok = /^https?:\/\//i.test(String(url || ''));
  if (ok) a.href = url; else a.removeAttribute('href');
  a.hidden = !ok;
}

/* Putting a stored value into a <select> without inventing or losing one.
   ----------------------------------------------------------------------
   A <select> can only show a value it has an <option> for. Handed anything
   else Chrome selects nothing: the control renders blank AND `.value` reads
   "", so collect() writes that blank back over what was stored. Both halves
   were watched happening in Chrome on a row stored as source "aruodas_lt",
   municipality "Utenos r.": the drawer showed two empty boxes, and pressing
   Išsaugoti without touching anything left source "" and municipality NULL.
   The opposite failure was the reported one: `CURRENT.source || 'evarzytynes'`
   turned a row with no source into a claim that it came from a portal this
   project is forbidden to fetch, and saving made the claim permanent.

   So a value is shown as itself or as its own absence, never as some other
   value:
   - nothing stored selects the explicit "nenurodytas" option. Every select
     that shows a stored value must carry one; it is not a source or a
     municipality and cannot be read as one.
   - something stored that the list does not contain gets an option of its own,
     carrying the value itself and marked as not being in the list. The datum
     stays visible, `.value` still equals what is stored so a save preserves
     it, and the operator can see there is something to correct — a spelling
     the list never had, or a source key that has been renamed.
   The option is built with createElement and textContent rather than
   innerHTML, so a stored value cannot be parsed as markup: the same rule esc()
   enforces everywhere else in this file, kept by not building markup at all. */
function showStoredValue(sel, stored) {
  sel.querySelectorAll('option[data-unlisted]').forEach((o) => o.remove());
  const v = stored === null || stored === undefined ? '' : String(stored);
  if (v !== '' && ![...sel.options].some((o) => o.value === v)) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = `${v} — nėra sąraše`;
    opt.dataset.unlisted = '1';
    sel.insertBefore(opt, sel.firstChild);
  }
  sel.value = v;
}

function openDrawer(c) {
  // A new object declares no source. It has not come from anywhere yet, and
  // the operator is the only one who knows where it came from — pre-selecting
  // a portal here is how a row acquires a provenance nobody asserted.
  CURRENT = c ? structuredClone(c) : {
    ref: null, source: '', flags: {}, scores: {}, costs: {}, checks: {},
    archived: false,
  };
  $('dRef').textContent = CURRENT.ref || 'NAUJAS';
  $('dTitle').textContent = CURRENT.locality || CURRENT.title || 'Naujas objektas';
  $('dName').value = CURRENT.title || '';
  showStoredValue($('dSource'), CURRENT.source);
  $('dUrl').value = CURRENT.url || '';
  $('dLocality').value = CURRENT.locality || '';
  $('dCad').value = CURRENT.cadastral_no || '';
  $('dPrice').value = CURRENT.price_eur ?? '';
  $('dArea').value = CURRENT.house_m2 ?? '';
  $('dPlot').value = CURRENT.plot_ares ?? '';
  $('dEnds').value = (CURRENT.auction_ends_at || '').slice(0, 10);
  $('dNotes').value = CURRENT.notes || '';
  showStoredValue($('dMuni'), CURRENT.municipality);
  $('btnDelete').style.visibility = CURRENT.id ? 'visible' : 'hidden';
  $('btnArchive').textContent = CURRENT.archived ? 'Grąžinti iš archyvo' : 'Archyvuoti';

  renderFlags(); renderScores(); renderCosts(); renderChecks();
  renderContacts(CURRENT);
  syncOpenAd(CURRENT.url);
  renderNature(CURRENT.nature);
  $('adviceBox').textContent = '';
  $('scrim').hidden = false; $('drawer').hidden = false;
}

function closeDrawer() {
  $('scrim').hidden = true; $('drawer').hidden = true; CURRENT = null;
}

function renderFlags() {
  const box = $('dFlags'); box.textContent = '';
  SCHEMA.hard_flags.forEach((f) => {
    const on = !!CURRENT.flags[f.key];
    const row = document.createElement('label');
    row.className = `frow${on ? ' is-on' : ''}`;
    row.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}><span>${esc(f.label)}</span>`;
    row.querySelector('input').onchange = (e) => {
      CURRENT.flags[f.key] = e.target.checked;
      row.classList.toggle('is-on', e.target.checked);
    };
    box.appendChild(row);
  });
}

function renderScores() {
  const box = $('dScores'); box.textContent = '';
  SCHEMA.criteria.forEach((c, i) => {
    const v = CURRENT.scores[c.key];
    const row = document.createElement('div');
    row.className = 'srow';
    row.innerHTML =
      `<label><span class="wswatch" style="background:${BAND[i]};display:inline-block;` +
      `margin-right:7px"></span>${esc(c.label)}</label>` +
      `<input type="range" min="0" max="10" step="1" value="${v ?? 0}">` +
      `<span class="val">${v ?? '—'}</span>`;
    const range = row.querySelector('input');
    const val = row.querySelector('.val');
    range.oninput = () => {
      CURRENT.scores[c.key] = Number(range.value);
      val.textContent = range.value;
    };
    box.appendChild(row);
  });
}

function renderCosts() {
  const box = $('dCosts'); box.textContent = '';
  SCHEMA.cost_lines.forEach((l) => {
    const row = document.createElement('div');
    row.className = 'crow';
    row.innerHTML = `<label>${esc(l.label)}</label>` +
      `<input type="number" min="0" step="50" value="${CURRENT.costs[l.key] ?? ''}">`;
    row.querySelector('input').oninput = (e) => {
      CURRENT.costs[l.key] = e.target.value === '' ? 0 : Number(e.target.value);
      updateCostTotal();
    };
    box.appendChild(row);
  });
  updateCostTotal();
}

function updateCostTotal() {
  const sub = SCHEMA.cost_lines.reduce((s, l) => s + (Number(CURRENT.costs[l.key]) || 0), 0);
  const total = sub * (1 + SETTINGS.contingency_pct);
  const el = $('dCostTotal');
  el.textContent = sub ? `${fmt(Math.round(total))} EUR` : '—';
  el.style.color = sub && total > SETTINGS.budget_ceiling_eur ? 'var(--ochre)' : 'var(--paper)';
}

function renderChecks() {
  const box = $('dChecks'); box.textContent = '';
  SCHEMA.checks.forEach((k, i) => {
    const row = document.createElement('label');
    row.className = 'krow';
    row.innerHTML =
      `<input type="checkbox" ${CURRENT.checks[k.key] ? 'checked' : ''}>` +
      `<span>${i + 1}. ${esc(k.label)}</span>` +
      `<span class="price">${esc(k.cost)}</span>` +
      (k.url ? `<a href="${esc(k.url)}" target="_blank" rel="noopener">atidaryti</a>` : '');
    row.querySelector('input').onchange = (e) => { CURRENT.checks[k.key] = e.target.checked; };
    if (k.url) row.querySelector('a').onclick = (e) => e.stopPropagation();
    box.appendChild(row);
  });
}

function collect() {
  return {
    source: $('dSource').value,
    url: $('dUrl').value || null,
    title: $('dName').value || null,
    municipality: $('dMuni').value || null,
    locality: $('dLocality').value || null,
    cadastral_no: $('dCad').value || null,
    price_eur: $('dPrice').value === '' ? null : Number($('dPrice').value),
    house_m2: $('dArea').value === '' ? null : Number($('dArea').value),
    plot_ares: $('dPlot').value === '' ? null : Number($('dPlot').value),
    auction_ends_at: $('dEnds').value || null,
    notes: $('dNotes').value || null,
    flags: CURRENT.flags, scores: CURRENT.scores,
    costs: CURRENT.costs, checks: CURRENT.checks,
    archived: !!CURRENT.archived,
  };
}

/* ------------------------------------------------------------------- wire */
function wire() {
  ['fQuery', 'fMinPrice', 'fMaxPrice', 'fMinScore', 'fMaxCost',
   'fNear', 'fRadius', 'fLake', 'fRiver'].forEach((id) =>
    $(id).addEventListener('input', () => {
      clearTimeout(TIMER); TIMER = setTimeout(loadCandidates, 300);
    }));
  ['fMuni', 'fVerdict', 'fSort', 'fArchived', 'fProfile', 'fMatchState'].forEach((id) =>
    $(id).addEventListener('change', loadCandidates));

  $('btnClear').onclick = () => {
    ['fQuery', 'fMinPrice', 'fMaxPrice', 'fMinScore', 'fMaxCost',
     'fNear', 'fRadius', 'fLake', 'fRiver'].forEach((i) => { $(i).value = ''; });
    ['fMuni', 'fVerdict', 'fProfile'].forEach((i) => { $(i).value = ''; });
    $('fSort').value = 'eur_per_point'; $('fArchived').checked = false;
    loadCandidates();
  };

  // Typing or pasting a URL arms the button immediately; clearing it hides the
  // button again. Without this the control would only agree with the field
  // after the drawer was closed and reopened.
  $('dUrl').addEventListener('input', () => syncOpenAd($('dUrl').value));

  $('btnAdd').onclick = () => openDrawer(null);
  $('btnClose').onclick = closeDrawer;
  $('scrim').onclick = closeDrawer;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('drawer').hidden) closeDrawer();
  });

  document.querySelectorAll('.tab').forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll('.tab').forEach((x) => x.classList.remove('is-on'));
      document.querySelectorAll('.tabpane').forEach((x) => x.classList.remove('is-on'));
      t.classList.add('is-on');
      document.querySelector(`[data-pane="${t.dataset.tab}"]`).classList.add('is-on');
    };
  });

  $('btnSave').onclick = async () => {
    try {
      const payload = collect();
      if (CURRENT.id) await api(`/candidates/${CURRENT.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
      else await api('/candidates', { method: 'POST', body: JSON.stringify(payload) });
      closeDrawer(); await loadCandidates(); toast('Išsaugota');
    } catch (e) { toast(`Nepavyko išsaugoti: ${e.message}`, true); }
  };

  $('btnArchive').onclick = async () => {
    if (!CURRENT.id) return toast('Pirma išsaugok objektą', true);
    try {
      await api(`/candidates/${CURRENT.id}`, {
        method: 'PATCH', body: JSON.stringify({ ...collect(), archived: !CURRENT.archived }),
      });
      closeDrawer(); await loadCandidates(); toast('Archyvas atnaujintas');
    } catch (e) { toast(e.message, true); }
  };

  $('btnDelete').onclick = async () => {
    if (!CURRENT.id || !confirm('Ištrinti objektą negrįžtamai?')) return;
    await api(`/candidates/${CURRENT.id}`, { method: 'DELETE' });
    closeDrawer(); await loadCandidates(); toast('Ištrinta');
  };

  /* The source is asked for, not assumed. It decides two things at once: what
     the row will say about where it came from, and which parser reads the
     price — api.paste routes on the declared key and only sniffs the text when
     there is none, and sniffing an auction notice pasted without its URL reads
     the market valuation where the auction parser reads the starting price
     (40000 against 25000 on the same property). This field used to arrive
     pre-set to `evarzytynes`, which answered both questions by guessing. The
     text stays in the box while the operator goes and picks. */
  $('btnPaste').onclick = async () => {
    const text = $('pText').value.trim();
    if (!text) return toast('Įklijuok skelbimo tekstą', true);
    if (!$('dSource').value) {
      document.querySelector('[data-tab="facts"]').click();
      $('dSource').focus();
      return toast('Nurodyk šaltinį (skiltis „Faktai“) — nuo jo priklauso kainos skaitymas', true);
    }
    try {
      const c = await api('/paste', {
        method: 'POST',
        body: JSON.stringify({ text, url: $('pUrl').value || null, source: $('dSource').value }),
      });
      $('pText').value = ''; $('pUrl').value = '';
      closeDrawer(); await loadCandidates();
      toast(`Sukurta ${c.ref}. Patikrink ištrauktus laukus.`);
    } catch (e) { toast(e.message, true); }
  };

  $('btnSaveSettings').onclick = async () => {
    try {
      SETTINGS = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({
          budget_ceiling_eur: Number($('sBudget').value),
          min_score: Number($('sMinScore').value),
          contingency_pct: Number($('sContingency').value) / 100,
        }),
      });
      await loadCandidates(); toast('Prielaidos išsaugotos');
    } catch (e) { toast(e.message, true); }
  };

  $('btnLocate').onclick = locate;
  $('btnAdvice').onclick = () => loadAdvice().catch((e) => toast(e.message, true));
  $('btnPoll').onclick = checkNow;
  $('btnSaveProfile').onclick = () => saveProfileEditor().catch((e) => toast(e.message, true));
  $('btnTestProfile').onclick = () => testProfile().catch((e) => toast(e.message, true));
  $('epPick').onchange = loadProfileEditor;
  $('epCentres').onchange = () => renderCentreResolution();
  $('btnResetProfiles').onclick = async () => {
    // Says what reset now actually does: it drops your edits to the built-in
    // profiles and leaves profiles you created yourself alone (api.reset_profiles).
    if (!confirm('Grąžinti numatytuosius profilius? Tavo atlikti jų pakeitimai '
                 + 'bus prarasti; tavo paties sukurti profiliai išliks.')) return;
    PROFILES = (await api('/profiles/reset', { method: 'POST' })).profiles;
    renderProfiles(); await loadCandidates(); toast('Profiliai atkurti');
  };

  $('btnRefresh').onclick = async () => {
    const b = $('btnRefresh');
    b.disabled = true; b.textContent = 'Siunčiama…';
    try {
      const r = await api('/refresh', { method: 'POST' });
      await loadMarket(); toast(`Atnaujinta ${r.municipalities} savivaldybių`);
    } catch (e) { toast(`Atnaujinti nepavyko: ${e.message}`, true); }
    b.disabled = false; b.textContent = 'Atnaujinti registro duomenis';
  };
}

/* ------------------------------------------------------------------- boot */
(async function boot() {
  try {
    SCHEMA = await api('/schema');
    SETTINGS = SCHEMA.settings;

    // All 60 municipalities: the default scope is the whole country.
    const all = SCHEMA.all_municipalities || SCHEMA.municipalities;
    const opts = all.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
    // Two selects, two different empty options, because "" means two different
    // things. On the filter it is the absence of a filter — every municipality
    // is included. On the drawer it is a property of one row: this candidate's
    // municipality is not recorded. Naming it in words rather than leaving it
    // as a bare dash is what showStoredValue() relies on to say "unknown"
    // without naming a municipality that would be read as a fact.
    $('fMuni').innerHTML = '<option value="">Visa Lietuva</option>' + opts;
    $('dMuni').innerHTML = '<option value="">— nenurodyta —</option>' + opts;

    $('sBudget').value = SETTINGS.budget_ceiling_eur;
    $('sMinScore').value = SETTINGS.min_score;
    $('sContingency').value = Math.round(SETTINGS.contingency_pct * 100);

    PROFILES = SCHEMA.profiles;   // renderProfiles() below fills both pickers

    // Collapse the filter panel wherever the rail is stacked above the content
    // rather than beside it. Below 1080px ten expanded controls meant scrolling
    // past every one of them to reach the first property — the app opened to
    // its controls instead of its content. The summary stays, and states how
    // many filters are active. This width is the same one styles.css reorders
    // the shell at; CSS cannot do the collapse itself, because `open` is an
    // attribute rather than a style.
    if (window.matchMedia('(max-width:1080px)').matches) $('filterPanel').open = false;

    renderWeights();
    renderProfiles();
    wire();
    await Promise.all([loadCandidates(), loadMarket(), loadIngestStatus()]);
    loadProfileEditor();

    // Registry stock moves once a day; the mailbox moves whenever a portal fires.
    setInterval(loadMarket, 15 * 60 * 1000);
    setInterval(() => { loadCandidates(); loadIngestStatus(); }, 3 * 60 * 1000);
  } catch (e) {
    toast(`Nepavyko paleisti: ${e.message}`, true);
  }
})();
