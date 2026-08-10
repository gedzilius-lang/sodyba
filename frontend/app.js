/* Sodyba Radar dashboard.
   No framework, no build step. State lives on the server; this file renders it. */

const $ = (id) => document.getElementById(id);
const api = async (path, opts = {}) => {
  const r = await fetch(`/api${path}`, {
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

async function loadCandidates() {
  const data = await api(`/candidates?${filterQuery()}`);
  const body = $('candBody');
  body.textContent = '';
  $('candCount').textContent = `${data.count} objekt${data.count === 1 ? 'as' : 'ai'}`;
  $('candEmpty').hidden = data.count > 0;

  data.items.forEach((c) => {
    const tr = document.createElement('tr');
    if (c.archived) tr.classList.add('is-archived');
    if (c.match_state === 'near') tr.classList.add('is-near');
    tr.onclick = () => openDrawer(c);

    const td = (html, cls) => {
      const d = document.createElement('td');
      if (cls) d.className = cls;
      if (html instanceof Node) d.appendChild(html); else d.innerHTML = html;
      return d;
    };
    tr.appendChild(td(`<span class="ref">${esc(c.ref)}</span>`));
    const chips = (c.profiles || [])
      .map((k) => `<span class="chip">${esc(profileName(k))}</span>`).join('') + duplicateChip(c.notes);
    tr.appendChild(td(
      `<span class="place">${esc(c.locality || c.title || '—')}` +
      `<small>${esc(c.municipality || '')} · ${esc(c.source)}</small>${chips}</span>`));
    tr.appendChild(td(fmt(c.price_eur), 'num'));
    tr.appendChild(td(coreBar(c)));
    tr.appendChild(td(c.weighted_score === null ? '—' : c.weighted_score.toFixed(2), 'num'));
    tr.appendChild(td(fmt(c.total_cost), 'num'));
    tr.appendChild(td(natureCell(c)));
    tr.appendChild(td(fmt(c.eur_per_point), 'num'));
    const misses = Object.values(c.misses || {}).flat();
    const why = misses.map((m) => m.text).join(' · ');
    tr.appendChild(td(
      `<span class="tag ${c.match_state}" title="${esc(why)}">` +
      `${MATCH_LT[c.match_state] || c.match_state}</span>` +
      (c.match_state === 'near' && why
        ? `<small class="miss">${esc(why)}</small>` : '')));
    tr.appendChild(td(
      `<span class="tag ${c.verdict}" title="${esc(c.verdict_reason)}">${VERDICT_LT[c.verdict]}</span>`));
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
    tr.innerHTML =
      `<td>${esc(m.municipality)}</td>` +
      `<td class="num">${fmt(m.total)}</td>` +
      `<td class="num">${fmt(m.power_and_water)}</td>` +
      `<td class="num">${(m.pct_power_water * 100).toFixed(1)}%</td>` +
      `<td class="num">${(m.pct_pre_1945 * 100).toFixed(1)}%</td>` +
      `<td class="num">${fmt(m.log_walls)}</td>` +
      `<td class="num">${m.rarity_index.toFixed(2)}</td>`;
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
    row.innerHTML =
      `<input type="checkbox" ${p.enabled ? 'checked' : ''} title="Įjungti profilį">` +
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

  const sel = $('fProfile');
  const keep = sel.value;
  sel.innerHTML = '<option value="">Visi profiliai</option>' +
    PROFILES.map((p) => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('');
  sel.value = keep;
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
async function pollMailbox(manual = false) {
  const btn = $('btnPoll');
  if (manual) { btn.disabled = true; btn.textContent = 'Tikrinama…'; }
  try {
    const r = await api('/ingest/mailbox', { method: 'POST' });
    if (r.status === 'skipped') {
      toast('IMAP nesukonfigūruotas — nustatyk SR_IMAP_* kintamuosius', true);
    } else if (r.status === 'error') {
      toast(`Pašto klaida: ${r.error}`, true);
    } else {
      const n = (r.created || []).length;
      toast(n ? `${n} nauji objektai` : `Naujų nėra (peržiūrėta ${r.scanned})`);
      await loadCandidates();
    }
  } catch (e) { toast(e.message, true); }
  await loadIngestStatus();
  if (manual) { btn.disabled = false; btn.textContent = 'Tikrinti dabar'; }
}

async function loadIngestStatus() {
  try {
    const r = await api('/ingest/log?limit=5');
    const mail = r.items.find((x) => x.source === 'mailbox');
    $('ingestStatus').textContent = mail
      ? `Paštas: ${mail.status} · ${mail.detail} · ${mail.ended_at}`
      : (SCHEMA.ingest.mailbox_configured
          ? 'Paštas sukonfigūruotas, dar netikrinta'
          : 'Paštas neįjungtas — žr. README');
  } catch { /* status line is cosmetic */ }
}

/* ----------------------------------------------------------------- drawer */
function openDrawer(c) {
  CURRENT = c ? structuredClone(c) : {
    ref: null, source: 'evarzytynes', flags: {}, scores: {}, costs: {}, checks: {},
    archived: false,
  };
  $('dRef').textContent = CURRENT.ref || 'NAUJAS';
  $('dTitle').textContent = CURRENT.locality || CURRENT.title || 'Naujas objektas';
  $('dName').value = CURRENT.title || '';
  $('dSource').value = CURRENT.source || 'evarzytynes';
  $('dUrl').value = CURRENT.url || '';
  $('dLocality').value = CURRENT.locality || '';
  $('dCad').value = CURRENT.cadastral_no || '';
  $('dPrice').value = CURRENT.price_eur ?? '';
  $('dArea').value = CURRENT.house_m2 ?? '';
  $('dPlot').value = CURRENT.plot_ares ?? '';
  $('dEnds').value = (CURRENT.auction_ends_at || '').slice(0, 10);
  $('dNotes').value = CURRENT.notes || '';
  $('dMuni').value = CURRENT.municipality || '';
  $('btnDelete').style.visibility = CURRENT.id ? 'visible' : 'hidden';
  $('btnArchive').textContent = CURRENT.archived ? 'Grąžinti iš archyvo' : 'Archyvuoti';

  renderFlags(); renderScores(); renderCosts(); renderChecks();
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

  $('btnPaste').onclick = async () => {
    const text = $('pText').value.trim();
    if (!text) return toast('Įklijuok skelbimo tekstą', true);
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
  $('btnPoll').onclick = () => pollMailbox(true);
  $('btnSaveProfile').onclick = () => saveProfileEditor().catch((e) => toast(e.message, true));
  $('btnTestProfile').onclick = () => testProfile().catch((e) => toast(e.message, true));
  $('epPick').onchange = loadProfileEditor;
  $('btnResetProfiles').onclick = async () => {
    if (!confirm('Grąžinti numatytuosius profilius? Tavo pakeitimai bus prarasti.')) return;
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
    $('fMuni').innerHTML = '<option value="">Visa Lietuva</option>' + opts;
    $('dMuni').innerHTML = '<option value="">—</option>' + opts;

    $('sBudget').value = SETTINGS.budget_ceiling_eur;
    $('sMinScore').value = SETTINGS.min_score;
    $('sContingency').value = Math.round(SETTINGS.contingency_pct * 100);

    PROFILES = SCHEMA.profiles;
    $('epPick').innerHTML =
      PROFILES.map((p) => `<option value="${esc(p.key)}">${esc(p.name)}</option>`).join('');

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
