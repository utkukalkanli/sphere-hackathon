// Dashboard front end. Two views side by side, like two processes with a link between them. Left
// what was given: one window with its own slider (brief, facts, input pictures, ground truth). Right
// what we made: the solid after the chosen pipeline step, with what that step removed drawn over it.
// On the boundary between them floats what the chosen step extracted (it can be dragged away).
// Underneath, in an area whose height can be dragged: the steps as a line in the middle of it (it
// wraps when there are too many for one row), and how the chosen step's solid differs from the truth. On the right one window with
// The boundary between the two views can be dragged. No build step, no framework.
import { createViewer, setView, fit, zoomBy, trackball, setTrackball, ghost, setGhost, faces, setFaces, section, setSection, VIEWS, COLORS } from '/static/viewer.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const VIEW_NAMES = Object.keys(VIEWS);
const DEFAULT_VIEW = 'view_6_iso_front_right_top';
const viewLabel = (name) => name.replace(/^view_\d+_/, '').replaceAll('_', ' ');

const icon = (path) => `<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const ICON = {
  prev: icon('<path d="M10 3.5 5.5 8l4.5 4.5"/>'),
  next: icon('<path d="M6 3.5 10.5 8 6 12.5"/>'),
  minus: icon('<path d="M3.5 8h9"/>'),
  plus: icon('<path d="M3.5 8h9M8 3.5v9"/>'),
  fit: icon('<path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10"/>'),
  orbit: icon('<circle cx="8" cy="8" r="5.5"/><ellipse cx="8" cy="8" rx="5.5" ry="2.2"/><ellipse cx="8" cy="8" rx="2.2" ry="5.5"/>'),
  diff: icon('<rect x="2.5" y="2.5" width="8" height="8" rx="1"/><rect x="5.5" y="5.5" width="8" height="8" rx="1" stroke-dasharray="2 1.7"/>'),
  faces: icon('<path d="M8 2.5 13 5v6l-5 2.5L3 11V5z"/><path d="M3 5l5 2.5L13 5M8 7.5v6"/>'),
  cut: icon('<path d="M2.5 11.5 8 9l5.5 2.5M2.5 4.5 8 2l5.5 2.5L8 7z"/><path d="M2.5 8 8 10.5 13.5 8" stroke-dasharray="2 1.7"/>'),
  order: icon('<path d="M3 4h10M3 8h7M3 12h4"/>'),
  down: icon('<path d="M4.5 6.5 8 10l3.5-3.5"/>'),
  ghost: icon('<path d="M3 5.5 8 3l5 2.5v5L8 13l-5-2.5z" stroke-dasharray="2 1.7"/>'),
  play: icon('<path d="M5 3.5v9l7-4.5z" fill="currentColor"/>'),
  stop: icon('<rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor"/>'),
};

const S = {
  datasets: [], runs: [], cases: {}, now: 0,
  dataset: null, caseName: null, runId: null,  // runId '' = committed outputs / no run
  source: 'truth',  // what the right window shows: 'brief', 'facts', an input picture or 'truth'
  sel: null,  // the step whose solid is shown; null = follow the latest
  diff: false,  // the big view shows the difference to the ground truth instead of the solid
};
const traces = new Map();  // "<run>/<case>" -> { events, loading, loaded, failed }
let live = { id: 0, rows: [] };  // rows: the steps on the slider  // bumped whenever another case or run comes on screen
let started = null;  // a run just launched here that /api/state may not list yet

// -- helpers ------------------------------------------------------------------------------------

const htmlCache = new WeakMap();
function setHTML(element, html) {  // skip identical updates: keeps focus, scroll and open <details> intact
  if (!element || htmlCache.get(element) === html) return;
  htmlCache.set(element, html);
  element.innerHTML = html;
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function toast(message) {
  const node = document.createElement('div');
  node.className = 'toast';
  node.setAttribute('role', 'alert');
  node.textContent = message;
  document.body.append(node);
  setTimeout(() => node.remove(), 5000);
}

const num = (value, digits = 2) => (value == null || !isFinite(value) ? '–'
  : Number(value).toLocaleString('en-US', { maximumFractionDigits: digits }));
function duration(seconds) {
  if (seconds == null || !isFinite(seconds)) return '–';
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}
const clock = (ts) => (ts ? new Date(ts * 1000).toLocaleString('en-GB', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '');
const words = (name) => String(name).replaceAll('_', ' ');
const isImage = (name) => /\.(png|jpe?g|gif|webp|svg)$/i.test(name);
const isStep = (name) => /\.(stp|step)$/i.test(name);

const dataset = () => S.datasets.find((d) => d.id === S.dataset);
const cases = () => S.cases[S.dataset] || [];
const currentCase = () => cases().find((c) => c.name === S.caseName);
const currentRun = () => S.runs.find((r) => r.id === S.runId);
const datasetRuns = () => S.runs.filter((r) => !r.dataset || r.dataset === S.dataset);
const runCase = (run, name) => run && run.cases.find((c) => c.name === name);
// The runs the run list offers: those of the case on screen (all of the set while no case is chosen)
const caseRuns = () => datasetRuns().filter((r) => !S.caseName || runCase(r, S.caseName) || r.id === S.runId);
// Volume score of the case on screen at the end of a run: 1 is the part's own volume
function runScore(run) {
  const c = currentCase(), rc = runCase(run, S.caseName);
  const truth = ((c && c.facts && c.facts.volume_mm3) || 0) / 1000;
  const volumes = ((rc && rc.stages) || []).map((s) => s.volume_cm3).filter(Boolean);
  const ours = volumes[volumes.length - 1];
  return truth && ours ? Math.min(ours, truth) / Math.max(ours, truth) : null;
}

// The .stp we are judged on for this case: from the selected run, else the committed file
function outputOf(c) {
  const run = currentRun();
  if (run) {
    const rc = runCase(run, c.name);
    return rc && rc.output ? { url: `/files/runs/${run.id}/${c.name}/${rc.output}` } : null;
  }
  return c.output ? { url: c.output.url, key: `${c.output.url}?${c.output.mtime}` } : null;
}

// -- reference renders --------------------------------------------------------------------------

// The renders come on a white sheet. Cut the sheet away (flood from the border) so the part sits
// on the dark interface instead of in a glaring white square.
const cutouts = new Map();
function cutout(src) {
  if (!cutouts.has(src)) {
    cutouts.set(src, (async () => {
      const bitmap = await createImageBitmap(await (await fetch(src)).blob());
      const scale = Math.min(1, 768 / Math.max(bitmap.width, bitmap.height));
      const w = Math.round(bitmap.width * scale), h = Math.round(bitmap.height * scale);
      const canvas = Object.assign(document.createElement('canvas'), { width: w, height: h });
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(bitmap, 0, 0, w, h);
      const image = ctx.getImageData(0, 0, w, h), px = image.data;
      const light = (i) => Math.min(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]);
      const gone = new Uint8Array(w * h), stack = [];
      const visit = (i) => { if (!gone[i] && light(i) >= 236) { gone[i] = 1; stack.push(i); } };
      for (let x = 0; x < w; x++) { visit(x); visit((h - 1) * w + x); }
      for (let y = 0; y < h; y++) { visit(y * w); visit(y * w + w - 1); }
      while (stack.length) {
        const i = stack.pop(), x = i % w;
        if (x > 0) visit(i - 1);
        if (x < w - 1) visit(i + 1);
        if (i >= w) visit(i - w);
        if (i < w * (h - 1)) visit(i + w);
      }
      for (let i = 0; i < w * h; i++) if (light(i) >= 250) gone[i] = 1;  // sheet enclosed by the part
      for (let i = 0; i < w * h; i++) {
        if (gone[i]) { px[i * 4 + 3] = 0; continue; }
        const x = i % w;  // soften the antialiased rim that touched the sheet
        const rim = (x > 0 && gone[i - 1]) || (x < w - 1 && gone[i + 1]) || (i >= w && gone[i - w]) || (i < w * (h - 1) && gone[i + w]);
        if (rim && light(i) > 180) px[i * 4 + 3] = Math.round(255 * (236 - Math.min(light(i), 236)) / 56);
      }
      ctx.putImageData(image, 0, 0);
      return URL.createObjectURL(await new Promise((resolve) => canvas.toBlob(resolve)));
    })().catch(() => src));
  }
  return cutouts.get(src);
}

function hydrateImages(root = document) {
  $$('img[data-cut]', root).forEach(async (img) => {
    const src = img.dataset.cut;
    if (img.dataset.done === src) return;
    const url = await cutout(src);
    if (img.dataset.cut === src) { img.src = url; img.dataset.done = src; }
  });
}

// -- STEP loading -------------------------------------------------------------------------------

let worker = null, jobs = new Map(), jobId = 0;
const stepCache = new Map();
function loadStep(url, key = url) {
  if (stepCache.has(key)) return stepCache.get(key);
  const promise = (async () => {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`could not load file (${response.status})`);
    const buffer = await response.arrayBuffer();
    const head = new TextDecoder().decode(buffer.slice(0, 64)).trimStart();
    if (!head.startsWith('ISO-10303-21')) throw new Error('not a STEP file');
    if (!worker) {
      worker = new Worker('/static/step-worker.js');
      worker.onmessage = ({ data }) => {
        const job = jobs.get(data.id);
        jobs.delete(data.id);
        if (job) data.ok ? job.resolve(data) : job.reject(new Error(data.error));
      };
    }
    return new Promise((resolve, reject) => {
      const id = ++jobId;
      jobs.set(id, { resolve, reject });
      worker.postMessage({ id, buffer });
    });
  })();
  stepCache.set(key, promise);
  promise.catch(() => stepCache.delete(key));
  if (stepCache.size > 32) stepCache.delete(stepCache.keys().next().value);
  return promise;
}

// Show a .stp in a viewport. The previous solid stays up until the next one is ready, so stepping
// through the pipeline swaps the part in place without a flash.
// options.before: the solid of the step before, drawn as what was removed. options.frame: a solid
// whose bounding box is framed instead (the first stage), so the part does not jump between steps.
// options.truth: the ground truth; the view then shows what the solid has too much and what it lacks.
async function showModel(viewport, url, key, color, empty = '', { before = null, frame = null, truth = null, ...options } = {}) {
  const stage = $('.stage', viewport);
  const id = url ? `${key || url}|${before ? before.url : ''}|${truth ? 'diff' : ''}` : '';
  if (stage.dataset.url === id) return;
  stage.dataset.url = id;
  const fresh = () => stage.isConnected && stage.dataset.url === id;
  const clear = (html) => {
    if (stage.viewer) stage.viewer.dispose();
    stage.viewer = null;
    stage.innerHTML = html;
    viewport.classList.remove('live');
  };
  if (!url) return clear(empty);
  if (!stage.viewer) stage.innerHTML = '<p class="note pulse">Loading…</p>';
  try {
    const optional = (model) => (model ? loadStep(model.url, model.key).catch(() => null) : null);
    const [solid, old, box, real] = await Promise.all([loadStep(url, key), optional(before), optional(frame), optional(truth)]);
    if (!fresh()) return;
    const viewer = await createViewer(stage, solid.meshes, color, {
      ...options, ghost: old && old.meshes, frame: box && box.measured,
      diff: real && { meshes: real.meshes, shift: solid.measured.min.map((v, i) => v - real.measured.min[i]) } });
    if (!fresh()) return viewer.dispose();
    if (stage.viewer) stage.viewer.dispose();
    stage.viewer = viewer;
    stage.replaceChildren(viewer.canvas);
    viewport.classList.add('live');
  } catch (error) {
    if (fresh()) clear(`<p class="note bad" title="${esc(error.message)}">Unreadable .stp</p>`);
  }
}

const viewportHTML = (id, label, tools = true) => `<div class="viewport" id="${id}">
  <div class="stage" role="img" aria-label="${label}"></div>
  ${tools ? `<div class="tools">
    <button data-diff aria-pressed="${S.diff}" title="Show what is too much and what is missing against the ground truth (D)" aria-label="Show the difference to the ground truth">${ICON.diff}</button>
    <button data-ghost aria-pressed="${ghost()}" title="Show what the step removed (G)" aria-label="Show what the step removed">${ICON.ghost}</button>
    <button data-faces aria-pressed="${faces()}" title="Every face in its own colour (C)" aria-label="Every face in its own colour">${ICON.faces}</button>
    <button data-cut aria-pressed="${section().axis >= 0}" title="Cut the solids open: X, Y, Z, off (X)" aria-label="Section plane">${cutLabel()}</button>
    <input type="range" id="cut" min="0" max="1" step="0.005" value="${section().at}" aria-label="Where the section plane is" ${section().axis < 0 ? 'hidden' : ''}>
    <button data-orbit aria-pressed="${trackball()}" title="Free rotation (T)" aria-label="Free rotation">${ICON.orbit}</button>
    <button data-zoom="0.8" title="Zoom out (−)" aria-label="Zoom out">${ICON.minus}</button>
    <button data-zoom="1.25" title="Zoom in (+)" aria-label="Zoom in">${ICON.plus}</button>
    <button data-fit title="Fit the part (F, or double-click)" aria-label="Fit the part">${ICON.fit}</button>
  </div>` : ''}</div>`;

function toggleOrbit() {
  setTrackball(!trackball());
  $$('[data-orbit]').forEach((button) => button.setAttribute('aria-pressed', trackball()));
}
function toggleDiff() {
  const c = currentCase();
  if (!c || !c.truth) return;
  S.diff = !S.diff;
  try { localStorage.setItem('diff', S.diff ? 'on' : 'off'); } catch { /* private mode */ }
  $$('[data-diff]').forEach((button) => button.setAttribute('aria-pressed', S.diff));
  renderSteps(c);
}
try { S.diff = localStorage.getItem('diff') === 'on'; } catch { /* private mode */ }

function toggleFaces() {
  setFaces(!faces());
  $$('[data-faces]').forEach((button) => button.setAttribute('aria-pressed', faces()));
}
const cutLabel = () => (section().axis < 0 ? ICON.cut : `<b>${'XYZ'[section().axis]}</b>`);
function cycleSection() {  // off, X, Y, Z, off
  setSection(section().axis === 2 ? -1 : section().axis + 1);
  $$('[data-cut]').forEach((button) => { button.setAttribute('aria-pressed', section().axis >= 0); button.innerHTML = cutLabel(); });
  $$('#cut').forEach((slider) => { slider.hidden = section().axis < 0; });
}
function toggleGhost() {
  setGhost(!ghost());
  $$('[data-ghost]').forEach((button) => button.setAttribute('aria-pressed', ghost()));
}

// -- routing ------------------------------------------------------------------------------------

function writeHash() {
  const hash = `#/${S.dataset}/${S.caseName || ''}${S.runId ? `?run=${S.runId}` : ''}`;
  if (location.hash !== hash) history.replaceState(null, '', hash);
}

function readHash() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [ds, name] = path.split('/');
  if (ds) S.dataset = ds;
  if (name) S.caseName = name;
  const run = new URLSearchParams(query || '').get('run');
  if (run) S.runId = run;
}

async function ensureCases(force = false) {
  await Promise.all(S.datasets.filter((d) => force || !S.cases[d.id]).map(async (d) => {
    S.cases[d.id] = (await api(`/api/cases?dataset=${d.id}`)).cases;
  }));
}

function normalise() {
  if (!S.datasets.some((d) => d.id === S.dataset)) S.dataset = S.datasets[0] && S.datasets[0].id;
  const runs = datasetRuns();
  if (runs.some((r) => r.id === started)) started = null;
  if (S.runId && S.runId !== started && !runs.some((r) => r.id === S.runId)) S.runId = null;
  if (S.runId == null) {  // the newest run that has this case; without a case, the newest run
    const run = S.caseName ? runs.find((r) => runCase(r, S.caseName)) : runs[0];
    S.runId = run ? run.id : '';
  }
  if (cases().length && !currentCase()) {
    const run = currentRun();
    const first = run && run.cases[0] && cases().find((c) => c.name === run.cases[0].name);
    S.caseName = (first || cases()[0]).name;
  }
}

// -- top bar ------------------------------------------------------------------------------------

const envelope = (c) => {
  const b = c.facts && c.facts.bounding_box_mm;
  return b ? `${num(b.x, 1)} × ${num(b.y, 1)} × ${num(b.z, 1)} mm` : '';
};
const generic = (c) => !c.title || /^mechanical part$/i.test(c.title) || c.title === c.name;

const caseLabel = (c) => `${c.number} · ${generic(c) ? envelope(c) || c.name : c.title}${c.tier ? ` · ${c.tier}` : ''}`;
const runLabel = (r) => `${clock(r.started_at)} · ${r.cases.length} case${r.cases.length === 1 ? '' : 's'} · ${r.status}`;
const noRun = () => (dataset() && dataset().committed ? 'Committed outputs' : 'None');
const grade = (score) => (score < 0.6 ? 'bad' : score < 0.9 ? 'warn' : '');

// The lists behind the two buttons of the bar: our own, so a case can be found by typing its number
// and every row can carry its score. One is open at a time.
let menu = null;
function openMenu(kind) {
  menu = menu === kind ? null : kind;
  const box = $('#menu');
  box.hidden = !menu;
  $$('[data-menu]').forEach((button) => button.setAttribute('aria-expanded', button.dataset.menu === menu));
  if (!menu) return;
  const anchor = $(`[data-menu="${menu}"]`).getBoundingClientRect();
  box.style.left = `${anchor.left}px`;
  box.style.top = `${anchor.bottom + 4}px`;
  box.innerHTML = `<input id="menu-filter" type="text" autocomplete="off" spellcheck="false" placeholder="${menu === 'case' ? 'Number or name' : 'Filter'}" aria-label="Filter"><div class="list" role="listbox"></div>`;
  renderMenu();
  const chosen = box.querySelector('[aria-selected="true"]');
  if (chosen) chosen.scrollIntoView({ block: 'center' });
  $('#menu-filter').focus();
  if (kind === 'case') refreshCases();
}
function renderMenu() {
  if (!menu) return;
  const list = $('#menu .list'), words = $('#menu-filter').value.trim().toLowerCase();
  const number = /^\d+$/.test(words);
  let html = '';
  if (menu === 'case') {
    const run = currentRun(), inRun = new Set(run ? run.cases.map((c) => c.name) : []);
    for (const d of S.datasets) {
      // a number finds the cases whose number starts with it; anything else is looked for in the row
      const rows = (S.cases[d.id] || []).filter((c) => !words || (number ? String(c.number).startsWith(words) : caseLabel(c).toLowerCase().includes(words)));
      if (!rows.length) continue;
      html += `<h4>${esc(d.label)} <small>${rows.length}</small></h4>` + rows.map((c) => `<button role="option" data-pick="${d.id}/${c.name}" aria-selected="${d.id === S.dataset && c.name === S.caseName}">
        <b>${c.number}</b><span>${esc(generic(c) ? envelope(c) || c.name : c.title)}</span><small>${esc(c.tier || '')}</small>
        <em class="${c.score ? grade(c.score.score) : ''}">${c.score ? c.score.score.toFixed(2) : ''}</em><i class="${d.id === S.dataset && inRun.has(c.name) ? 'on' : ''}"></i></button>`).join('');
    }
  } else {
    const rows = [{ id: '', label: noRun(), score: null }, ...caseRuns().map((r) => ({ id: r.id, label: runLabel(r), score: runScore(r) }))].filter((r) => !words || r.label.toLowerCase().includes(words));
    html = rows.map((r) => `<button class="run" role="option" data-pick="${esc(r.id)}" aria-selected="${r.id === (S.runId || '')}"><span>${esc(r.label)}</span><em class="${r.score == null ? '' : grade(r.score)}">${r.score == null ? '' : r.score.toFixed(2)}</em></button>`).join('');
  }
  setHTML(list, html || '<p class="note">Nothing found</p>');
  if (!list.querySelector('.at')) {
    const first = list.querySelector(words ? 'button' : '[aria-selected="true"]') || list.querySelector('button');
    if (first) first.classList.add('at');
  }
}
function moveInMenu(offset) {
  const rows = $$('#menu .list button'), at = rows.findIndex((row) => row.classList.contains('at'));
  const next = rows[Math.max(0, Math.min(rows.length - 1, at + offset))];
  if (!next) return;
  if (rows[at]) rows[at].classList.remove('at');
  next.classList.add('at');
  next.scrollIntoView({ block: 'nearest' });
}
function pick(value) {
  const kind = menu;
  openMenu(kind);  // closes it
  if (kind === 'case') { const [ds, name] = value.split('/'); return selectCase(name, ds); }
  Object.assign(S, { runId: value, sel: null });
  const run = currentRun();  // a run of other cases: go to its first case
  if (run && run.cases.length && !runCase(run, S.caseName)) S.caseName = run.cases[0].name;
  render();
}

function renderBar() {
  const run = currentRun(), current = currentCase();
  const running = run && run.status === 'running';
  setHTML($('#bar'), `
    <div class="group">
      <button class="icon" data-move="-1" title="Previous case (↑)" aria-label="Previous case">${ICON.prev}</button>
      <button class="pick" data-menu="case" aria-haspopup="listbox" aria-expanded="${menu === 'case'}" aria-label="Test case"><span>${esc(current ? caseLabel(current) : 'No case')}</span>${ICON.down}</button>
      <button class="icon" data-move="1" title="Next case (↓)" aria-label="Next case">${ICON.next}</button>
      <button class="icon" data-scores aria-pressed="${scoresOpen}" title="Scores of all cases, worst first (S)" aria-label="Scores of all cases">${ICON.order}</button>
    </div>
    <label class="field"><span>Run</span>
      <button class="pick" data-menu="run" aria-haspopup="listbox" aria-expanded="${menu === 'run'}" aria-label="Run"><span>${esc(run ? runLabel(run) : noRun())}</span>${ICON.down}</button>
    </label>
    <span class="spacer"></span>
    <span class="offline-note">Dashboard server is not responding</span>
    ${running && run.launched_by === 'ui' ? `<button data-action="stop">${ICON.stop}Stop</button>` : ''}
    <button class="primary" data-action="run" title="Run scripts/main.py on this case with tracing">${ICON.play}Run this case</button>`);
}

// -- columns ------------------------------------------------------------------------------------

function renderMain() {
  const main = $('#main'), c = currentCase();
  if (!c) return setHTML(main, `<p class="note center">${S.datasets.length ? 'Loading cases…' : 'No datasets found under artifacts/inputs or datasets/eval.'}</p>`);
  const key = [S.dataset, c.name, S.runId].join('|');
  if (main.dataset.key !== key) {
    main.dataset.key = key;
    $$('.stage', main).forEach((stage) => stage.viewer && stage.viewer.dispose());
    live = { id: live.id + 1, rows: [] };
    htmlCache.delete(main);
    main.innerHTML = `
      <aside id="result" aria-label="Input and ground truth">
        <div class="view">${viewportHTML('out', 'Ground truth', false)}
          <img id="big" alt="">
          <div class="tools" id="pic-tools">
            <button data-pic="0.5" title="Zoom out (right click)" aria-label="Zoom out">${ICON.minus}</button>
            <button data-pic="2" title="Zoom in (click)" aria-label="Zoom in">${ICON.plus}</button>
            <button data-pic="0" title="Whole picture" aria-label="Whole picture">${ICON.fit}</button>
          </div>
          <div id="doc"></div>
          <div class="hud" id="result-head"></div></div>
        <div id="sources"></div>
      </aside>
      <section id="centre" class="${c.truth ? '' : 'no-truth'}" aria-label="The solid after the chosen step">
        ${viewportHTML('ours', 'Our solid after the chosen step. Drag to rotate, shift-drag to pan, scroll to zoom.')}
        <div class="hud"><div id="stage-label"></div><div id="steps-note"></div></div>
        <div class="hud end" id="steps-status"></div>
        <div class="grip" role="separator" aria-orientation="vertical" title="Drag to resize, double-click to reset"></div>
      </section>
      <section id="extracted" aria-label="What the chosen step extracted"><div id="step-detail"></div></section>
      <footer id="foot"><div id="timeline"></div><div id="metrics"></div></footer>
      <div class="grip row" role="separator" aria-orientation="horizontal" title="Drag to resize, double-click to reset"></div>`;
    placeExtracted();
    new ResizeObserver(layoutTimeline).observe($('#timeline'));
    loadBrief(c);
    startMeasuring(c);
  }
  renderResult(c);
  renderSteps(c);
}

// -- the brief and the facts ------------------------------------------------------------------------

function fmt(value) {
  if (value == null) return '–';
  if (typeof value === 'number') return num(value);
  if (typeof value !== 'object') return esc(value);
  if (Array.isArray(value)) {
    return value.map((v) => (Array.isArray(v) ? `(${v.map(fmt).join(', ')})` : fmt(v))).join(Array.isArray(value[0]) ? '<br>' : ', ') || '–';
  }
  return Object.entries(value).map(([k, v]) => `${fmt(v)} ${esc(words(k).toLowerCase())}`).join(' · ') || '–';
}

// A list of records as a table; anything else on one line
function tableHTML(value) {
  if (!Array.isArray(value) || typeof value[0] !== 'object' || value[0] == null) return `<p class="mono">${fmt(value)}</p>`;
  const columns = [...new Set(value.flatMap((item) => Object.keys(item)))];
  return `<div class="wide"><table class="grid"><thead><tr>${columns.map((k) => `<th>${esc(words(k))}</th>`).join('')}</tr></thead>
    <tbody>${value.map((item) => `<tr>${columns.map((k) => `<td>${fmt(item[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function factsHTML(facts) {
  if (!facts) return '<p class="note">This case has no facts.json.</p>';
  const box = facts.bounding_box_mm;
  const spec = [
    ['Envelope', box && `${num(box.x)} × ${num(box.y)} × ${num(box.z)} <small>mm</small>`],
    ['Volume', facts.volume_mm3 != null && `${num(facts.volume_mm3 / 1000)} <small>cm³</small>`],
    ['Surface', facts.surface_area_mm2 != null && `${num(facts.surface_area_mm2 / 100)} <small>cm²</small>`],
    ['Solids', facts.solids],
    ['Through holes', facts.through_holes_genus],
    ['Faces · edges', facts.faces != null && `${num(facts.faces)} · ${num(facts.edges)}`],
    ['Face types', facts.face_types && fmt(facts.face_types)],
  ].filter(([, v]) => v != null && v !== false);
  const shown = new Set(['bounding_box_mm', 'volume_mm3', 'surface_area_mm2', 'solids', 'through_holes_genus', 'faces', 'edges', 'face_types']);
  const rest = Object.entries(facts).filter(([k, v]) => !shown.has(k) && !k.startsWith('_')
    && v != null && (typeof v !== 'object' || Object.keys(v).length));
  const feature = ([key, value]) => `<h3>${esc(words(key))}</h3>${tableHTML(value)}`;
  return `<dl class="spec">${spec.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${rest.map(feature).join('')}`;
}

// The description is the prompt our pipeline reads. Dimensions are lifted out of the prose so the
// numbers can be found without reading every sentence.
function markdown(text) {
  const inline = (line) => line.split(/(\b\d+(?:\.\d+)?(?:\s?(?:mm|cm\^?[23]|°|%))?(?![\w.]*\d))/)
    .map((part, i) => (i % 2 ? `<span class="q">${esc(part)}</span>` : esc(part))).join('')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  let html = '', list = false;
  for (const line of text.split('\n')) {
    const item = line.match(/^\s*[-*]\s+(.*)/), heading = line.match(/^(#{1,4})\s+(.*)/);
    if (list && !item) { html += '</ul>'; list = false; }
    if (item) { html += `${list ? '' : '<ul>'}<li>${inline(item[1])}</li>`; list = true; }
    else if (heading) html += heading[1].length === 1 ? '' : `<h3>${inline(heading[2])}</h3>`;  // the title is the column heading
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (list ? '</ul>' : '');
}

const imageSrc = (c, name) => `${dataset().inputs_url}/${c.name}/${name === 'context' ? 'context.png' : `images/${name}.png`}`;

function loadBrief(c) {
  const shown = live;
  fetch(`/api/description?dataset=${S.dataset}&case=${c.name}`).then((r) => r.text()).then((text) => {
    shown.brief = text;
    if (shown === live) renderResult(c);
  }, () => {});
}

// -- right: one window on the brief, the facts, the input pictures and the ground truth ------------

const isPicture = (name) => !['brief', 'facts', 'truth'].includes(name);
const sourceLabel = (name) => ({ brief: 'Brief', facts: 'Facts', truth: 'Ground truth' }[name] || viewLabel(name));
function sourcesOf(c) {
  const images = [...c.views.map((file) => file.replace(/\.png$/, '')), ...(c.context ? ['context'] : [])];
  return ['brief', 'facts', ...images, ...(c.truth ? ['truth'] : [])];
}
// The chosen one if this case has it; else the truth, else a picture (the practice set has no truth)
function sourceOf(c) {
  const list = sourcesOf(c);
  if (list.includes(S.source)) return S.source;
  return list.includes('truth') ? 'truth' : list.includes(DEFAULT_VIEW) ? DEFAULT_VIEW : list.find(isPicture) || 'brief';
}

function selectSource(name, turn = true) {
  const c = currentCase();
  if (!c || !sourcesOf(c).includes(name)) return;
  S.source = name;
  renderResult(c);
  if (turn && VIEWS[name]) setView(name);
}

function startMeasuring(c) {
  const id = live.id, state = live.state = { truth: c.truth ? undefined : null, ours: null };
  if (c.truth) {
    settle(loadStep(c.truth.url, `${c.truth.url}?${c.truth.mtime}`)).then((m) => { state.truth = m; if (id === live.id) renderMetrics(c, state); });
  }
}

function renderResult(c) {
  const view = $('#result .view');
  if (!view) return;
  const list = sourcesOf(c), at = sourceOf(c);
  setHTML($('#result-head'), `<h2>${at === 'truth' ? '<i class="sw truth"></i>' : ''}${esc(sourceLabel(at))}</h2>`);
  view.dataset.show = at === 'truth' ? 'model' : isPicture(at) ? 'image' : 'doc';
  if (c.truth) showModel($('#out'), c.truth.url, `${c.truth.url}?${c.truth.mtime}`, COLORS.truth);  // stays loaded behind the others
  if (isPicture(at)) {
    const img = $('#big'), src = imageSrc(c, at);
    if (img.dataset.cut !== src) { resetBig(); img.dataset.cut = src; img.alt = `Reference render, ${viewLabel(at)}`; hydrateImages(view); }
  } else if (at === 'brief') setHTML($('#doc'), `<article class="prose">${live.brief == null ? '' : markdown(live.brief)}</article>`);
  else if (at === 'facts') setHTML($('#doc'), factsHTML(c.facts));
  renderSources(c, list, at);
}

// The slider under the window: brief, facts, the pictures, the truth
function renderSources(c, list, at) {
  const box = $('#sources'), signature = `${S.dataset}/${c.name}/${list.join()}`;
  if (box.dataset.rows !== signature) {
    box.dataset.rows = signature;
    box.innerHTML = `<div class="track" style="--n:${list.length}">
      <div class="rail"></div>
      <input type="range" id="source" min="0" max="${list.length - 1}" step="1" aria-label="What the window shows">
      ${list.map((name, i) => `<button class="stop" data-source="${name}" title="${esc(sourceLabel(name))}${VIEWS[name] ? ` — turns the 3D views to this side (${VIEW_NAMES.indexOf(name) + 1})` : ''}">
        <i class="dot"></i>${isPicture(name) ? `<img alt="${esc(viewLabel(name))}" src="${imageSrc(c, name)}" data-cut="${imageSrc(c, name)}">` : `<span class="name">${name === 'truth' ? 'Truth' : sourceLabel(name)}</span>`}</button>`).join('')}
    </div>`;
    hydrateImages(box);
  }
  const index = list.indexOf(at), slider = $('#source');
  if (Number(slider.value) !== index) slider.value = index;
  slider.setAttribute('aria-valuetext', sourceLabel(at));
  $$('.stop', box).forEach((stop, i) => stop.setAttribute('aria-current', i === index));
}

// -- the boundary between the two views: drag to resize ----------------------------------------------

function setSplit(width) {  // null: back to the share the stylesheet gives the left view
  const value = width == null ? null : Math.round(Math.max(260, Math.min(innerWidth * 0.65, width)));
  if (value == null) document.body.style.removeProperty('--split'); else document.body.style.setProperty('--split', `${value}px`);
  try { value == null ? localStorage.removeItem('panel-split') : localStorage.setItem('panel-split', value); } catch { /* private mode */ }
}
try { if (Number(localStorage.getItem('panel-split'))) setSplit(Number(localStorage.getItem('panel-split'))); } catch { /* private mode */ }

// The area under the views: as high as its content unless it was dragged
function setFoot(height) {
  const value = height == null ? null : Math.round(Math.max(70, Math.min(innerHeight * 0.6, height)));
  if (value == null) document.body.style.removeProperty('--foot'); else document.body.style.setProperty('--foot', `${value}px`);
  try { value == null ? localStorage.removeItem('panel-foot') : localStorage.setItem('panel-foot', value); } catch { /* private mode */ }
}
try { if (Number(localStorage.getItem('panel-foot'))) setFoot(Number(localStorage.getItem('panel-foot'))); } catch { /* private mode */ }

// The window on what the step extracted floats on the boundary. Dragged by its title it stays where
// it was put (as an offset from the boundary); a double-click on the title puts it back.
let floatAt = [0, 0];
try { floatAt = JSON.parse(localStorage.getItem('extracted-at')) || [0, 0]; } catch { /* private mode */ }
function placeExtracted(at = floatAt) {
  floatAt = at;
  if ($('#extracted')) $('#extracted').style.translate = `${at[0]}px ${at[1]}px`;
}

function drag(event, handle, move) {
  event.preventDefault();
  handle.setPointerCapture(event.pointerId);
  handle.classList.add('on');
  const done = () => { handle.classList.remove('on'); handle.removeEventListener('pointermove', move); };
  handle.addEventListener('pointermove', move);
  handle.addEventListener('pointerup', done, { once: true });
  handle.addEventListener('pointercancel', done, { once: true });
}
document.addEventListener('pointerdown', (event) => {
  const grip = event.target.closest('.grip'), title = event.target.closest('#extracted h2');
  if (grip) {
    const box = $('#main').getBoundingClientRect(), row = grip.classList.contains('row');
    drag(event, grip, (e) => (row ? setFoot(box.bottom - e.clientY) : setSplit(e.clientX - box.left)));
  } else if (title) {
    const from = [event.clientX - floatAt[0], event.clientY - floatAt[1]];
    drag(event, title, (e) => {
      placeExtracted([e.clientX - from[0], e.clientY - from[1]]);
      try { localStorage.setItem('extracted-at', JSON.stringify(floatAt)); } catch { /* private mode */ }
    });
  }
});
document.addEventListener('dblclick', (event) => {
  const grip = event.target.closest('.grip');
  if (grip) grip.classList.contains('row') ? setFoot(null) : setSplit(null);
  if (event.target.closest('#extracted h2')) { placeExtracted([0, 0]); try { localStorage.removeItem('extracted-at'); } catch { /* private mode */ } }
});

const settle = (promise) => promise.then((data) => data.measured, () => null);

// Called on every render with the solid on screen: measures it again whenever it changes, so the
// diff follows the slider. With a ground-truth solid the two are also laid on each other.
function measureOurs(c, model, name = '') {
  const shown = live, state = live.state;
  const key = model ? model.key || model.url : '';
  if (!state) return;
  if (shown.out === key) {  // the same solid under another step's name
    if (state.name !== name) { state.name = name; renderMetrics(c, state); }
    return;
  }
  shown.out = key;
  Object.assign(state, { ours: model ? undefined : null, overlap: null, name });
  if (model) {
    const current = () => live === shown && shown.out === key;
    loadStep(model.url, model.key).then(async (ours) => {
      if (!current()) return;
      state.ours = ours.measured;
      renderMetrics(c, state);
      if (!c.truth) return;
      const truth = await loadStep(c.truth.url, `${c.truth.url}?${c.truth.mtime}`);
      const result = await compare(key, ours, truth);
      if (current()) { state.overlap = result; renderMetrics(c, state); }
    }).catch(() => { if (current()) { state.ours = state.ours || null; renderMetrics(c, state); } });
  }
  renderMetrics(c, state);
}

const overlaps = new Map();
function compare(key, ours, truth) {
  if (!overlaps.has(key)) {
    const solid = ({ meshes, measured }) => ({ meshes: meshes.map(({ position, index }) => ({ position, index })), min: measured.min, max: measured.max });
    overlaps.set(key, new Promise((resolve, reject) => {
      const id = ++jobId;
      jobs.set(id, { resolve, reject });
      worker.postMessage({ id, compare: { a: solid(ours), b: solid(truth) } });
    }).then((data) => data.overlap));
    if (overlaps.size > 64) overlaps.delete(overlaps.keys().next().value);
  }
  return overlaps.get(key);
}

function delta(expected, actual) {
  if (expected == null || actual == null || !isFinite(expected) || !isFinite(actual) || !expected) return '<td></td>';
  const pct = (actual - expected) / expected * 100, abs = Math.abs(pct);
  const text = abs.toFixed(abs < 10 ? 1 : 0);
  return `<td class="${abs <= 1 ? 'good' : abs <= 5 ? 'warn' : 'bad'}">${Number(text) === 0 ? '±' : pct > 0 ? '+' : '−'}${text} %</td>`;
}

function renderMetrics(c, state) {
  const facts = c.facts || {}, box = facts.bounding_box_mm, ours = state.ours, truth = state.truth;
  const size = box ? [box.x, box.y, box.z] : (truth && truth.bbox) || [];
  const volume = facts.volume_mm3 ?? (truth && truth.volume);
  const rows = [
    ...['X', 'Y', 'Z'].map((axis, i) => [`Size ${axis}`, 'mm', size[i], ours && ours.bbox[i]]),
    ['Volume', 'cm³', volume / 1000, ours && ours.volume / 1000],
    ['Surface', 'cm²', (facts.surface_area_mm2 ?? (truth && truth.area)) / 100, ours && ours.area / 100],
    ['Faces', '', facts.faces ?? (truth && truth.faces), ours && ours.faces],
  ];
  // one number for how close the solid is: what the local score uses, and the real overlap
  const share = (part) => (volume ? ` <small>· ${num(part / volume * 100, 1)} %</small>` : '');
  const o = state.overlap;
  const scores = [
    ours && volume ? ['Volume score', num(Math.min(ours.volume, volume) / Math.max(ours.volume, volume), 3), 'min / max of the two volumes: what tools/eval_hull.py reports'] : null,
    o ? ['Overlap (IoU)', num(o.iou, 3), `sampled with ${num(o.points)} points`] : null,
    o ? ['Excess', `${num(o.onlyA / 1000)} <small>cm³</small>${share(o.onlyA)}`, 'in our solid, not in the part; the percentage is of the part\'s volume'] : null,
    o ? ['Missing', `${num(o.onlyB / 1000)} <small>cm³</small>${share(o.onlyB)}`, 'in the part, not in our solid; the percentage is of the part\'s volume'] : null,
  ].filter(Boolean);
  const cell = (value) => (value == null || !isFinite(value) ? '<td class="none">–</td>' : `<td>${num(value)}</td>`);
  const note = ours === undefined || (ours && c.truth && !o) ? '<p class="note pulse">Measuring…</p>' : '';
  setHTML($('#metrics'), `${scores.length ? `<dl class="scores">${scores.map(([label, value, hint]) => `<div title="${esc(hint)}"><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>` : ''}
  <table class="metrics">
    <thead><tr><th></th>${rows.map(([label, unit]) => `<th>${label} <small>${unit}</small></th>`).join('')}</tr></thead>
    <tbody><tr><th><i class="sw truth"></i>Truth</th>${rows.map((r) => cell(r[2])).join('')}</tr>
      <tr><th><i class="sw ours"></i>Ours</th>${rows.map((r) => cell(r[3])).join('')}</tr>
      <tr><th>Diff</th>${rows.map((r) => delta(r[2], r[3])).join('')}</tr></tbody>
  </table>${note}`);
}

// -- middle: steps ------------------------------------------------------------------------------

function buildTrace(events) {
  const t = { plan: [], skips: new Map(), steps: new Map(), list: [], start: null, end: null, status: 'running', error: null, last: null };
  for (const e of events) {
    if (t.start == null) t.start = e.ts;
    t.last = e.ts;
    if (e.t === 'case_end') Object.assign(t, { end: e.ts, status: e.status, error: e.error });
    else if (e.t === 'plan') t.plan = e.steps || [];
    else if (e.t === 'skip') t.skips.set(e.name, e.reason);
    else if (e.t === 'step_start') {
      const parent = t.steps.get(e.parent);
      const step = { ...e, start: e.ts, end: null, status: 'running', depth: parent ? parent.depth + 1 : 0, logs: [], artifacts: [] };
      t.steps.set(e.id, step);
      t.list.push(step);
    } else if (e.t === 'step_end') {
      const step = t.steps.get(e.id);
      if (step) Object.assign(step, { end: e.ts, status: e.status, output: e.output, metrics: e.metrics, error: e.error });
    } else if (e.t === 'log' && t.steps.get(e.step)) t.steps.get(e.step).logs.push(e);
    else if (e.t === 'artifact' && e.role !== 'output' && t.steps.get(e.step)) t.steps.get(e.step).artifacts.push(e);
  }
  return t;
}

async function fetchTrace(run, name) {
  const key = `${run}/${name}`;
  const entry = traces.get(key) || { events: [], loading: false };
  traces.set(key, entry);
  if (entry.loading) return;
  entry.loading = true;
  try {
    const data = await api(`/api/events?run=${encodeURIComponent(run)}&case=${encodeURIComponent(name)}&after=${entry.events.length}`);
    entry.events.push(...data.events);
    entry.loaded = true;
    entry.failed = null;
  } catch (error) { entry.failed = error.message; } finally { entry.loading = false; }
  if (S.runId === run && S.caseName === name) renderSteps(currentCase());
}

const raw = (value) => `<pre>${esc(typeof value === 'string' ? value : JSON.stringify(value, null, 2))}</pre>`;
const unitOf = (key) => (key.match(/_(cm3|mm3|mm2|mm|deg|px)$/) || [])[1];
const pretty = (unit) => ({ cm3: 'cm³', mm3: 'mm³', mm2: 'mm²', deg: '°' }[unit] || unit);
function pairHTML([key, value]) {
  const unit = unitOf(key);
  return `<dt>${esc(words(unit ? key.slice(0, -unit.length - 1) : key))}</dt>
    <dd>${typeof value === 'number' ? num(value, 3) : fmt(value)}${unit ? ` <small>${pretty(unit)}</small>` : ''}</dd>`;
}

// The .json files a step attached (holes, rounds, pockets): fetched once, drawn as tables
const attached = new Map();
function attachedJSON(url) {
  if (!attached.has(url)) {
    attached.set(url, undefined);
    fetch(url).then((r) => r.json()).then((data) => attached.set(url, data), () => attached.set(url, null))
      .then(() => renderSteps(currentCase()));
  }
  return attached.get(url);
}

// A cut along one direction reads two opposite views. Both are shown, the second one mirrored the
// way the agent mirrors it, so the two and the joined silhouette under them line up.
const PAIRS = { front_back: ['front', 'back', 'X'], right_left: ['right', 'left', 'X'], top_bottom: ['top', 'bottom', 'Y'] };
function pairHTML2(s) {
  const pair = PAIRS[(s.name.match(/^cut_(front_back|right_left|top_bottom)$/) || [])[1]], c = currentCase();
  if (!pair || !c) return '';
  const view = (side) => (c.views.find((file) => file.endsWith(`_${side}.png`)) || '').replace(/\.png$/, '');
  const figure = (side, flip) => {
    const name = view(side);
    return name ? `<figure><button class="art plain" data-lightbox="${imageSrc(c, name)}"><img alt="${side} view" data-cut="${imageSrc(c, name)}" ${flip ? `style="scale: ${flip === 'X' ? '-1 1' : '1 -1'}"` : ''}></button>
      <figcaption>${side}${flip ? ' <small>mirrored</small>' : ''}</figcaption></figure>` : '';
  };
  return `<div class="pair">${figure(pair[0])}${figure(pair[1], pair[2])}</div>`;
}

// What the chosen step took from the inputs: its pictures, its lists, its numbers and what it said
function detailHTML(s, base, time) {
  const joined = pairHTML2(s);
  const images = s.artifacts.filter((a) => isImage(a.name));
  const lists = s.artifacts.filter((a) => /\.json$/i.test(a.name));
  // the same number is often both in the output (mm3) and in the metrics (cm3): keep the metric
  const stem = (key) => (unitOf(key) ? key.slice(0, -unitOf(key).length - 1) : key);
  const named = new Set(Object.keys(s.metrics || {}).map(stem));
  const pairs = [...Object.entries(s.output && typeof s.output === 'object' ? s.output : {}).filter(([key]) => !named.has(stem(key))),
    ...Object.entries(s.metrics || {})];
  const data = [s.input !== undefined ? `<h4>Input</h4>${raw(s.input)}` : '', s.output != null ? `<h4>Output</h4>${raw(s.output)}` : ''].join('');
  return [
    s.error ? `<pre class="bad">${esc(s.error)}</pre>` : '',
    joined,
    ...images.map((a) => `<figure><button class="art" data-lightbox="${esc(`${base}/${a.path}`)}" title="${esc(a.name)}">
      <img alt="${esc(a.name)}" src="${esc(`${base}/${a.path}`)}"></button>${joined && /^silhouette_/.test(a.name) ? '<figcaption>Both joined <small>cut only where both are empty</small></figcaption>' : ''}</figure>`),
    ...lists.map((a) => {
      const value = attachedJSON(`${base}/${a.path}`);
      return value == null ? '' : `<h3>${esc(words(a.name.replace(/\.json$/i, '')))}${Array.isArray(value) ? ` <small>${value.length}</small>` : ''}</h3>${tableHTML(value)}`;
    }),
    s.logs.length ? `<ul class="said">${s.logs.map((l) => `<li class="${l.level === 'info' ? '' : 'warn'}">${esc(l.msg)}</li>`).join('')}</ul>` : '',
    `<dl class="kv">${pairs.map(pairHTML).join('')}<dt>Time</dt><dd>${time}</dd></dl>`,
    data ? `<details class="fold"><summary>Raw</summary>${data}</details>` : '',
  ].join('');
}

function renderSteps(c) {
  if (!$('#timeline') || !c) return;
  const run = currentRun(), output = outputOf(c);
  const truth = c.facts && c.facts.volume_mm3;
  const rows = [];
  let note = '', status = '', running = false;
  if (run) {
    const key = `${run.id}/${c.name}`, entry = traces.get(key);
    if (!entry) fetchTrace(run.id, c.name);
    if (!entry || !entry.loaded) note = entry && entry.failed ? `<p class="note bad">Could not load the trace: ${esc(entry.failed)}</p>` : '<p class="note pulse">Loading the trace…</p>';
    else if (!entry.events.length) note = '<p class="note">Not part of this run.</p>';
    else {
      const t = buildTrace(entry.events), base = `/files/runs/${run.id}/${c.name}`;
      running = run.status === 'running' && t.status === 'running';
      const now = Date.now() / 1000;
      const state = running ? 'running' : t.status === 'running' ? 'interrupted' : t.status;
      status = `<span class="state ${state}">${state}</span> ${duration((t.end || (running ? now : t.last)) - t.start)}`;
      if (t.error) note = `<pre class="bad">${esc(t.error)}</pre>`;
      let model = null, lastRun = null, volumeWas = '';
      // Every case shows the same stops: the plan. A step the plan does not list (a retry, a cut
      // repeated inside a check) is folded into the stop it happened in.
      const plan = fullPlan(t.plan);
      const groups = plan.map(() => []), home = new Map();
      let at = 0, here = 0;
      t.list.forEach((s) => {
        if (home.has(s.parent)) { home.set(s.id, home.get(s.parent)); groups[home.get(s.parent).at].push(s); return; }
        const j = plan.findIndex((p, i) => i >= at && p.name === s.name);
        if (j >= 0) { here = j; at = j + 1; }
        home.set(s.id, { at: here, own: j >= 0 });
        groups[here].push(s);
      });
      plan.forEach((p, index) => {
        const steps = groups[index], s = steps.find((x) => x.name === p.name && home.get(x.id).own && !t.steps.get(x.parent));
        if (!s) {  // has not run: still to come, switched off, or passed over
          const state = p.off ? 'off' : index < here || t.skips.has(p.name) ? 'skipped' : running ? 'pending' : 'not run';
          const why = t.skips.get(p.name) || p.off || { skipped: 'Skipped in this case.', pending: 'Not started yet.', 'not run': 'The case ended before this step.' }[state];
          rows.push({ id: `plan:${p.name}`, raw: p.name, about: about({ name: p.name }), model, before: null, changed: false, state,
            n: String(rows.length + 1).padStart(2, '0'), name: words(p.name.replace(/^cut_/, '')), ratio: '', chips: '',
            detail: () => `<p class="note">${esc(why)}</p>` });
          return;
        }
        // The stop's solid: what the step itself left, or a folded step that went through with its own cuts
        const root = (x) => { while (t.steps.get(x.parent)) x = t.steps.get(x.parent); return x; };
        const counts = (x) => x === s || (root(x) !== s && root(x).status === 'ok');
        const stage = steps.filter(counts).flatMap((x) => x.artifacts.filter((a) => a.role === 'stage' && isStep(a.name))).pop();
        const before = stage ? model : null;  // only a step that left a new solid removed something
        if (stage) model = { url: `${base}/${stage.path}` };
        const m = s.metrics || {}, end = Math.max(...steps.map((x) => x.end || (running ? now : t.last)));
        const time = duration(end - s.start);
        const ratio = truth && m.volume_cm3 != null ? m.volume_cm3 * 1000 / truth : null;
        const was = volumeWas;
        if (ratio != null) volumeWas = `×${ratio.toFixed(ratio < 10 ? 2 : 1)}`;
        const open = steps.some((x) => !x.end);
        rows.push({
          was,
          id: s.id, raw: s.name, about: about(s), model, before, changed: Boolean(stage),
          state: !open ? s.status : running ? 'running' : 'interrupted',
          n: String(rows.length + 1).padStart(2, '0'), name: words(s.name.replace(/^cut_/, '')),
          ratio: ratio == null ? '' : `×${ratio.toFixed(ratio < 10 ? 2 : 1)}`,
          chips: [
            m.removed_cm3 ? `<i class="sw ghost"></i>removed ${num(m.removed_cm3)} cm³` : '',
            ratio != null ? `${num(ratio)} × true volume` : '',
          ].filter(Boolean).join(' · '),
          detail: () => detailHTML(s, base, time) + steps.filter((x) => x !== s).map((x) =>
            `<h3>${esc(words(x.name.replace(/^cut_/, '')))}</h3>${detailHTML(x, base, duration((x.end || (running ? now : t.last)) - x.start))}`).join(''),
        });
        lastRun = rows[rows.length - 1];
      });
      const last = lastRun;  // the exported file is the last step's solid, tidied
      if (last && output && !running && t.status === 'ok') Object.assign(last, { model: output, before: null });
      if (!running) rows.forEach((r) => r.model && loadStep(r.model.url, r.model.key).catch(() => {}));  // warm up, so scrubbing is instant
    }
  } else if (!output) note = '<p class="note">No output yet. Run this case to see its steps.</p>';
  if (!rows.length && output) {
    rows.push({ id: 'output', raw: '', about: '', model: output, before: null, changed: true, state: 'ok', n: '', name: 'Output', ratio: '', chips: '',
      detail: () => (run ? '' : '<p class="note">Run this case to see its steps.</p>') });
  }
  if (running && !rows.length) note = '<p class="note pulse">Running…</p>';

  // Which solid is on screen: the chosen step; else the final result, or while the run is still
  // going the newest step that left a solid
  const chosen = rows.find((r) => r.id === S.sel) || (running && [...rows].reverse().find((r) => r.changed)) || [...rows].reverse().find((r) => !r.id.startsWith('plan:')) || rows[rows.length - 1];
  live.rows = rows;
  live.chosen = chosen ? chosen.id : null;
  setHTML($('#steps-status'), status);
  const diff = S.diff && c.truth ? { url: c.truth.url, key: `${c.truth.url}?${c.truth.mtime}` } : null;
  const legend = '<i class="sw ours"></i>as the part · <i class="sw excess"></i>too much under this face · <i class="sw missing"></i>missing';
  setHTML($('#stage-label'), chosen ? `<span class="n">${chosen.n}</span><b>${esc(chosen.name)}</b><span class="chips">${diff ? legend : chosen.chips}</span>` : '');
  setHTML($('#steps-note'), note);
  renderTimeline(rows, chosen);
  const detail = chosen ? chosen.detail() : '';
  setHTML($('#step-detail'), detail && `<h2>${esc(chosen.name)}</h2>${detail}`);
  hydrateImages($('#step-detail'));
  measureOurs(c, chosen && chosen.model, chosen ? chosen.name : '');
  const first = rows.find((r) => r.model);
  live.frame = run && first ? first.model : null;
  showModel($('#ours'), chosen && chosen.model && chosen.model.url, chosen && chosen.model && chosen.model.key, COLORS.ours,
    note ? '' : `<p class="note">${running ? 'Waiting for the first solid…' : 'No solid to show yet.'}</p>`,
    { before: chosen && chosen.before, frame: live.frame, truth: diff });
}

// The stops every case has, in order. A trace brings its own plan (hull_agent.planned); names it
// lacks (runs traced before a step was planned, or before there was a plan) are put in from here,
// so every case of every run shows the same stops.
const STOPS = ['bounding_box', 'cut_front_back', 'cut_right_left', 'cut_top_bottom', 'cut_iso', 'isometric_support', 'check_views',
  'cut_holes', 'open_mouths', 'evidence', 'cut_rounds', 'cut_curved', 'level_faces', 'model_cuts', 'shading_depth', 'cut_pockets',
  'split_faces', 'export_step'];
function fullPlan(given) {
  const plan = given.slice();
  STOPS.forEach((name, i) => {
    if (plan.some((p) => p.name === name)) return;
    const after = STOPS.slice(0, i).reverse().map((n) => plan.findIndex((p) => p.name === n)).find((k) => k >= 0);
    plan.splice(after == null ? 0 : after + 1, 0, { name });
  });
  return plan;
}

// One line on what each step does, shown when the pointer is on its stop. A step can bring its own with
// trace.step(..., description="..."); this list covers the steps the pipeline has today.
const SIDES = { front_back: 'front and back', right_left: 'right and left', top_bottom: 'top and bottom', iso: 'isometric' };
const ABOUT = [
  [/^bounding_box$/, () => 'A box with the facts.json dimensions'],
  [/^cut_(front_back|right_left|top_bottom|iso)$/, (m) => `Cuts away what the ${SIDES[m[1]]} views show as empty`],
  [/^retry_(.+)_first$/, (m) => `Cuts again, ${SIDES[m[1]] || words(m[1])} first`],
  [/^check_views$/, () => 'Compares the solid with the silhouettes'],
  [/^cut_holes$/, () => 'Cuts the cylindrical holes listed in facts.json'],
  [/^open_mouths$/, () => 'Opens listed holes whose mouth a view draws under our surface'],
  [/^isometric_support$/, () => 'Cuts with the isometric silhouette too, where the straight cuts left far too much'],
  [/^evidence$/, () => 'Numbers the areas of the views and what the solid has under each'],
  [/^model_cuts$/, () => 'Cuts a model proposes, each kept only if the views fit better'],
  [/^cut_rounds$/, () => 'Makes square sections round along boss and bore axes'],
  [/^cut_curved$/, () => 'Carries a curved face on under a flat ring the side views left around an axis'],
  [/^cut_pockets$/, () => 'Cuts pockets whose outline the views draw under our surface'],
  [/^split_faces$/, () => 'Splits a face where the views draw a line across it'],
  [/^level_faces$/, () => 'Cuts each flat face down to its lowest level'],
  [/^shading_depth$/, () => 'Takes pocket depths from the shading of the isometric views'],
  [/^export_step$/, () => 'Merges coplanar faces and writes the .stp'],
];
function about(step) {
  if (step.meta && step.meta.description) return step.meta.description;
  for (const [pattern, text] of ABOUT) { const match = step.name.match(pattern); if (match) return text(match); }
  return '';
}

// The line of steps takes as many rows as it needs for every stop to keep a readable width. On one
// row it is a slider that can be dragged; on several the stops are clicked (or the arrow keys used).
const STOP_WIDTH = 74;
function layoutTimeline() {
  const track = $('#timeline .track');
  if (!track) return;
  const count = $$('.stop', track).length, fit = Math.max(1, Math.floor($('#timeline').clientWidth / STOP_WIDTH));
  const lines = Math.ceil(count / fit), columns = Math.ceil(count / lines);
  if (track.style.getPropertyValue('--n') !== String(columns)) track.style.setProperty('--n', columns);
  track.classList.toggle('wrapped', lines > 1);
}

// The steps as a slider. It is rebuilt only when the steps change, so a drag is never interrupted.
function renderTimeline(rows, chosen) {
  const timeline = $('#timeline');
  const signature = JSON.stringify(rows.map((r) => [r.id, r.name, r.state, r.ratio, r.changed, r.about]));
  if (timeline.dataset.rows !== signature) {
    timeline.dataset.rows = signature;
    timeline.innerHTML = rows.length < 2 ? '' : `<div class="track" style="--n:${rows.length}">
      <div class="rail"><i></i></div>
      <input type="range" id="scrub" min="0" max="${rows.length - 1}" step="1" aria-label="Pipeline step">
      ${rows.map((r) => `<button class="stop ${r.changed ? 'cut' : ''} ${r.state.replace(' ', '-')}" data-sel="${r.id}">
        <i class="dot"></i><span class="name">${esc(r.name)}</span>
        <span class="v ${r.state === 'ok' ? '' : `state ${r.state}`}">${r.state === 'ok' ? r.ratio : r.state === 'pending' ? '' : r.state}</span></button>`).join('')}
    </div>`;
  }
  const scrub = $('#scrub');
  if (!scrub) return;
  layoutTimeline();
  const at = Math.max(0, rows.indexOf(chosen));
  if (Number(scrub.value) !== at) scrub.value = at;
  scrub.setAttribute('aria-valuetext', chosen ? chosen.name : '');
  $('.rail i', timeline).style.width = `${at / (rows.length - 1) * 100}%`;
  $$('.stop', timeline).forEach((stop, i) => {
    stop.setAttribute('aria-current', i === at);
    stop.classList.toggle('done', i <= at);
  });
}

// With the pointer on a stop: what the step does, and the solid before and after it (what the step
// removed is drawn over the second one). The two small views follow the camera of the big one.
let tipFor = null;
function hideTip() {
  if (tipFor == null) return;
  tipFor = null;
  const tip = $('#tip');
  $$('.stage', tip).forEach((stage) => stage.viewer && stage.viewer.dispose());
  tip.hidden = true;
  tip.innerHTML = '';
}
function showTip(row, stop) {
  if (tipFor === row.id) return;
  hideTip();
  const pair = row.changed && row.before && row.model;
  if (!row.about && !pair) return;
  tipFor = row.id;
  const tip = $('#tip'), mini = (id) => `<div class="viewport" id="${id}"><div class="stage"></div></div>`;
  tip.innerHTML = `${row.about ? `<p>${esc(row.about)}</p>` : ''}${pair ? `<div class="pair">
    <figure>${mini('tip-before')}<figcaption>Before <b>${row.was}</b></figcaption></figure>
    <figure>${mini('tip-after')}<figcaption>After <b>${row.ratio}</b></figcaption></figure></div>` : ''}`;
  tip.hidden = false;
  const at = stop.getBoundingClientRect(), width = tip.offsetWidth;
  tip.style.left = `${Math.max(10, Math.min(innerWidth - width - 10, at.left + at.width / 2 - width / 2))}px`;
  tip.style.bottom = `${innerHeight - at.top + 10}px`;
  if (!pair) return;
  const options = { interactive: false, frame: live.frame };
  showModel($('#tip-before'), row.before.url, row.before.key, COLORS.ours, '', options);
  showModel($('#tip-after'), row.model.url, row.model.key, COLORS.ours, '', { ...options, before: row.before });
}
document.addEventListener('pointermove', (event) => {
  const timeline = event.target.closest && event.target.closest('#timeline');
  if (!timeline || event.pointerType === 'touch') return hideTip();
  const stops = $$('.stop', timeline);
  let stop = event.target.closest('.stop');
  if (!stop && event.target.id === 'scrub' && stops.length) {  // the slider lies over the dots
    const box = $('.track', timeline).getBoundingClientRect();
    stop = stops[Math.max(0, Math.min(stops.length - 1, Math.floor((event.clientX - box.left) / box.width * stops.length)))];
  }
  const row = stop && live.rows.find((r) => r.id === stop.dataset.sel);
  if (row) showTip(row, stop); else hideTip();
});
document.documentElement.addEventListener('pointerleave', hideTip);

// A step that cuts along one view pair shows that view, if the right window is on a picture
const STEP_VIEW = [[/front|back/, 'view_0_front'], [/right|left/, 'view_3_right'], [/top|bottom/, 'view_4_top'], [/iso/, 'view_6_iso_front_right_top']];
function selectStep(id) {
  S.sel = id;
  renderSteps(currentCase());
  const row = live.rows.find((r) => r.id === id);
  const view = row && /^cut_|^retry_/.test(row.raw) && (STEP_VIEW.find(([pattern]) => pattern.test(row.raw)) || [])[1];
  const c = currentCase();
  if (view && c && isPicture(sourceOf(c))) selectSource(view, false);
}

function moveStep(offset) {
  const index = live.rows.findIndex((r) => r.id === live.chosen);
  const next = live.rows[Math.max(0, Math.min(live.rows.length - 1, index + offset))];
  if (next) selectStep(next.id);
}

// -- overlay ------------------------------------------------------------------------------------

function closeOverlay() { $('#overlay').className = ''; $('#overlay').innerHTML = ''; }
function openOverlay(src) {
  $('#overlay').className = 'open';
  $('#overlay').innerHTML = `<img src="${esc(src)}" alt="">`;
}

// -- events -------------------------------------------------------------------------------------

// -- scores -------------------------------------------------------------------------------------
// One panel over the left of the big view: every case of the set that has a score, worst first, with the
// score after each step as small bars. The case list itself stays in order of number.
let scoresOpen = false;
function toggleScores() {
  scoresOpen = !scoresOpen;
  $$('[data-scores]').forEach((button) => button.setAttribute('aria-pressed', scoresOpen));
  renderScores();
  if (scoresOpen) refreshCases();
}
// The scores come with the case lists. They are read again when a list that shows them opens, and
// every few seconds while the scores panel is open, so an eval running elsewhere shows up by itself.
let casesAt = 0;
async function refreshCases() {
  casesAt = Date.now();
  try { await ensureCases(true); } catch { return; }
  renderScores();
  renderMenu();
}
const mean = (list) => list.reduce((sum, c) => sum + c.score.score, 0) / list.length;
function renderScores() {
  const panel = $('#scores');
  panel.hidden = !scoresOpen;
  if (!scoresOpen) { shownFor = null; return; }
  const scored = cases().filter((c) => c.score).sort((a, b) => a.score.score - b.score.score || a.number - b.number);
  const tiers = ['simple', 'medium', 'complex'].map((t) => [t, scored.filter((c) => c.tier === t)]).filter(([, list]) => list.length);
  const bars = (steps) => `<span class="bars">${steps.map((v) => `<i style="height:${Math.max(6, Math.round(v * 100))}%"></i>`).join('')}</span>`;
  const rows = panel.querySelector('.rows'), scroll = rows ? rows.scrollTop : 0;
  setHTML(panel, scored.length ? `
    <header><b>${mean(scored).toFixed(3)}</b><span>mean of ${scored.length} of ${cases().length}</span>
      ${tiers.map(([t, list]) => `<span>${t} <b>${mean(list).toFixed(2)}</b></span>`).join('')}</header>
    <div class="rows">${scored.map((c) => `<button data-goto="${c.name}" aria-current="${c.name === S.caseName}" title="${c.score.iou ? 'Overlap with the ground truth (IoU)' : 'Volume score'} · after each step: ${c.score.steps.map((v) => v.toFixed(2)).join(' ')}">
      <b class="${c.score.score < 0.6 ? 'bad' : c.score.score < 0.9 ? 'warn' : ''}">${c.score.score.toFixed(2)}</b>${bars(c.score.steps)}<span>${c.number}</span><small>${esc(c.tier || (generic(c) ? '' : c.title))}</small></button>`).join('')}</div>`
    : '<p class="note">No scores yet. Run tools/eval_hull.py or a case.</p>');
  if (panel.querySelector('.rows')) panel.querySelector('.rows').scrollTop = scroll;
  if (shownFor !== S.caseName) {  // follow the chosen case, but leave the scroll alone otherwise
    shownFor = S.caseName;
    const current = panel.querySelector('[aria-current="true"]');
    if (current) current.scrollIntoView({ block: 'nearest' });
  }
}
let shownFor = null;

function render() {
  normalise();
  writeHash();
  renderBar();
  renderScores();
  renderMenu();
  renderMain();
}

function selectCase(name, datasetId = S.dataset) {
  if (datasetId !== S.dataset) Object.assign(S, { dataset: datasetId, runId: null });
  hideTip();
  Object.assign(S, { caseName: name, sel: null, runId: null });  // normalise() picks this case's newest run
  render();
}

function moveCase(offset) {
  const list = cases(), next = list[list.findIndex((c) => c.name === S.caseName) + offset];
  if (next) selectCase(next.name);
}

document.addEventListener('click', async (event) => {
  if (event.target.closest('#overlay')) return closeOverlay();
  const target = event.target.closest('button');
  if (!target) return;
  const d = target.dataset;
  if (d.move) return moveCase(Number(d.move));
  if (d.source) return selectSource(d.source);
  if (d.zoom) return zoomBy(Number(d.zoom));
  if (d.pic) return picTool(Number(d.pic));
  if (d.fit !== undefined) return fit();
  if (d.orbit !== undefined) return toggleOrbit();
  if (d.ghost !== undefined) return toggleGhost();
  if (d.faces !== undefined) return toggleFaces();
  if (d.cut !== undefined) return cycleSection();
  if (d.scores !== undefined) return toggleScores();
  if (d.menu) return openMenu(d.menu);
  if (d.pick !== undefined) return pick(d.pick);
  if (d.goto) return selectCase(d.goto);
  if (d.diff !== undefined) return toggleDiff();
  if (d.lightbox) return openOverlay(d.lightbox);
  if (d.sel) return selectStep(d.sel);
  if (d.action === 'run') {
    try {
      const { id } = await api('/api/run', { dataset: S.dataset, only: [S.caseName], target: 'run' });
      started = id;
      Object.assign(S, { runId: id, sel: null });
      await poll();
    } catch (error) { toast(`Could not start the run: ${error.message}`); }
    return;
  }
  if (d.action === 'stop') return api('/api/stop', { run: S.runId }).then(poll, (e) => toast(`Could not stop the run: ${e.message}`));
});

// The picture in the left window: a click zooms in at the pointer, a right click (or shift-click) zooms out,
// scroll or pinch zooms too, drag or a sideways scroll pans. It never opens full screen.
const pan = { k: 1, x: 0, y: 0, drag: null };
function placeBig() {
  const img = $('#big');
  if (!img) return;
  img.style.transform = pan.k === 1 ? '' : `translate(${pan.x}px, ${pan.y}px) scale(${pan.k})`;
  img.classList.toggle('zoomed', pan.k !== 1);
}
function resetBig() { Object.assign(pan, { k: 1, x: 0, y: 0, drag: null }); placeBig(); }
function clampBig(img) {
  const w = img.offsetWidth, h = img.offsetHeight;
  pan.x = Math.min(0, Math.max(w - w * pan.k, pan.x));
  pan.y = Math.min(0, Math.max(h - h * pan.k, pan.y));
}
function zoomBig(img, event, factor) {
  const box = img.parentElement.getBoundingClientRect(), px = event.clientX - box.left, py = event.clientY - box.top;
  const k = Math.min(40, Math.max(1, pan.k * factor));
  pan.x = px - (px - pan.x) * k / pan.k;
  pan.y = py - (py - pan.y) * k / pan.k;
  pan.k = k;
  clampBig(img);
  placeBig();
}
function picTool(factor) {  // the buttons at the lower right: about the middle of the window
  const img = $('#big');
  if (!factor) return resetBig();
  const box = img.parentElement.getBoundingClientRect();
  zoomBig(img, { clientX: box.left + box.width / 2, clientY: box.top + box.height / 2 }, factor);
}
document.addEventListener('wheel', (event) => {
  const img = event.target;
  if (img.id !== 'big') return;
  event.preventDefault();
  if (!event.ctrlKey && (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY))) {  // sideways scroll pans
    pan.x -= event.deltaX || event.deltaY;
    clampBig(img);
    return placeBig();
  }
  zoomBig(img, event, Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0025)));
}, { passive: false });
document.addEventListener('pointerdown', (event) => {
  if (event.target.id !== 'big' || event.button !== 0) return;
  event.preventDefault();
  pan.drag = { x: event.clientX - pan.x, y: event.clientY - pan.y, x0: event.clientX, y0: event.clientY, moved: false };
  event.target.setPointerCapture(event.pointerId);
});
document.addEventListener('pointermove', (event) => {
  const drag = pan.drag;
  if (!drag || event.target.id !== 'big') return;
  if (!drag.moved && Math.hypot(event.clientX - drag.x0, event.clientY - drag.y0) < 4) return;
  drag.moved = true;
  pan.x = event.clientX - drag.x;
  pan.y = event.clientY - drag.y;
  clampBig(event.target);
  placeBig();
});
document.addEventListener('pointerup', (event) => {
  const drag = pan.drag;
  pan.drag = null;
  if (drag && !drag.moved && event.target.id === 'big') zoomBig(event.target, event, event.shiftKey || event.altKey ? 0.5 : 2);
});
document.addEventListener('pointercancel', () => { pan.drag = null; });
document.addEventListener('contextmenu', (event) => {
  if (event.target.id !== 'big') return;
  event.preventDefault();
  zoomBig(event.target, event, 0.5);
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'menu-filter') { htmlCache.delete($('#menu .list')); return renderMenu(); }
  if (event.target.id === 'cut') return setSection(section().axis, Number(event.target.value));
  if (event.target.id === 'source') { const c = currentCase(); return c && selectSource(sourcesOf(c)[event.target.value]); }
  if (event.target.id === 'scrub' && live.rows[event.target.value]) selectStep(live.rows[event.target.value].id);
});

document.addEventListener('pointerdown', (event) => {  // a click anywhere else closes the open list
  if (menu && !event.target.closest('#menu, [data-menu]')) openMenu(menu);
});

document.addEventListener('keydown', (event) => {
  if (menu) {  // the open list takes the keys
    const act = { Escape: () => openMenu(menu), ArrowDown: () => moveInMenu(1), ArrowUp: () => moveInMenu(-1), PageDown: () => moveInMenu(10), PageUp: () => moveInMenu(-10),
      Enter: () => { const at = $('#menu .list .at'); if (at) pick(at.dataset.pick); } }[event.key];
    if (act) { event.preventDefault(); act(); }
    return;
  }
  if (event.key === 'Escape') { if (scoresOpen) toggleScores(); return closeOverlay(); }
  if (event.target.matches('select, textarea, input:not([type=range])') || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.target.matches('input[type=range]') && event.key.startsWith('Arrow')) return;  // the slider moves itself
  const act = {
    ArrowRight: () => moveStep(1), ArrowLeft: () => moveStep(-1), ']': () => moveStep(1), '[': () => moveStep(-1),
    ArrowDown: () => moveCase(1), ArrowUp: () => moveCase(-1),
    '+': () => zoomBy(1.25), '=': () => zoomBy(1.25), '-': () => zoomBy(0.8), f: fit, 0: fit, t: toggleOrbit, g: toggleGhost, d: toggleDiff, c: toggleFaces, x: cycleSection, s: toggleScores,
  }[event.key] || (/^[1-8]$/.test(event.key) && (() => selectSource(VIEW_NAMES[Number(event.key) - 1])));
  if (!act) return;
  event.preventDefault();
  act();
});

window.addEventListener('hashchange', async () => { readHash(); await ensureCases(); render(); });

// -- polling ------------------------------------------------------------------------------------

let polling = false, again = false;
async function poll() {
  if (polling) { again = true; return; }  // the answer on its way may be older than what was just done
  polling = true;
  try {
    const state = await api('/api/state');
    const wasRunning = S.runs.filter((r) => r.status === 'running').map((r) => r.id);
    Object.assign(S, { datasets: state.datasets, runs: state.runs, now: state.now });
    normalise();
    const stale = scoresOpen && Date.now() - casesAt > 5000;
    if (stale) casesAt = Date.now();
    await ensureCases(stale || wasRunning.some((id) => (S.runs.find((r) => r.id === id) || {}).status !== 'running'));
    render();
    const run = currentRun();
    if (run && S.caseName) {
      const entry = traces.get(`${run.id}/${S.caseName}`);
      const pending = entry && entry.loaded && !entry.events.length && runCase(run, S.caseName);
      if (entry && (run.status === 'running' || pending || wasRunning.includes(run.id))) fetchTrace(run.id, S.caseName);
    }
    document.body.classList.remove('offline');
  } catch (error) {
    document.body.classList.add('offline');
  } finally {
    polling = false;
    if (again) { again = false; poll(); }
  }
}

readHash();
await poll();
setInterval(() => { if (!document.hidden) poll(); }, 2000);
