// Dashboard front end. Three columns: the input (reference renders, description, facts), our solid
// with the pipeline steps that built it, and the ground truth. No build step, no framework.
import { createViewer, setView, fit, zoomBy, VIEWS, COLORS } from '/static/viewer.js';

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
  play: icon('<path d="M5 3.5v9l7-4.5z" fill="currentColor"/>'),
  stop: icon('<rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor"/>'),
};

const S = {
  datasets: [], runs: [], cases: {}, now: 0,
  dataset: null, caseName: null, runId: null,  // runId '' = committed outputs / no run
  image: DEFAULT_VIEW,
  sel: null,  // the step whose solid is shown; null = follow the latest
};
const traces = new Map();  // "<run>/<case>" -> { events, loading, loaded, failed }
let live = { id: 0, rows: [] };  // bumped whenever another case or run comes on screen

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
async function showModel(viewport, url, key, color, empty = '', options = {}) {
  const stage = $('.stage', viewport);
  if (stage.dataset.url === (url || '')) return;
  stage.dataset.url = url || '';
  const fresh = () => stage.isConnected && stage.dataset.url === (url || '');
  const clear = (html) => {
    if (stage.viewer) stage.viewer.dispose();
    stage.viewer = null;
    stage.innerHTML = html;
    viewport.classList.remove('live');
  };
  if (!url) return clear(empty);
  if (!stage.viewer) stage.innerHTML = '<p class="note pulse">Loading…</p>';
  try {
    const { meshes } = await loadStep(url, key);
    if (!fresh()) return;
    const viewer = await createViewer(stage, meshes, color, options);
    if (!fresh()) return viewer.dispose();
    if (stage.viewer) stage.viewer.dispose();
    stage.viewer = viewer;
    stage.replaceChildren(viewer.canvas);
    viewport.classList.add('live');
  } catch (error) {
    if (fresh()) clear(`<p class="note bad" title="${esc(error.message)}">Unreadable .stp</p>`);
  }
}

const viewportHTML = (id, label) => `<div class="viewport" id="${id}">
  <div class="stage" role="img" aria-label="${label}"></div>
  <div class="tools">
    <button data-zoom="0.8" title="Zoom out (−)" aria-label="Zoom out">${ICON.minus}</button>
    <button data-zoom="1.25" title="Zoom in (+)" aria-label="Zoom in">${ICON.plus}</button>
    <button data-fit title="Fit the part (F, or double-click)" aria-label="Fit the part">${ICON.fit}</button>
  </div></div>`;

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
  if (S.runId == null || (S.runId && !runs.some((r) => r.id === S.runId))) S.runId = runs[0] ? runs[0].id : '';
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

function renderBar() {
  const ds = dataset(), run = currentRun();
  const inRun = new Set(run ? run.cases.map((c) => c.name) : []);
  const running = run && run.status === 'running';
  setHTML($('#bar'), `
    <div class="group">
      <button class="icon" data-move="-1" title="Previous case (←)" aria-label="Previous case">${ICON.prev}</button>
      <select id="case-select" aria-label="Test case">
        ${S.datasets.map((d) => `<optgroup label="${esc(d.label)} (${d.count})">
          ${(S.cases[d.id] || []).map((c) => `<option value="${d.id}/${c.name}" ${d.id === S.dataset && c.name === S.caseName ? 'selected' : ''}>
            ${esc(`${c.number} · ${generic(c) ? envelope(c) || c.name : c.title}${c.tier ? ` · ${c.tier}` : ''}`)}${d.id === S.dataset && inRun.has(c.name) ? ' •' : ''}</option>`).join('')}</optgroup>`).join('')}
      </select>
      <button class="icon" data-move="1" title="Next case (→)" aria-label="Next case">${ICON.next}</button>
    </div>
    <label class="field"><span>Run</span>
      <select id="run-select">
        <option value="">${ds && ds.committed ? 'Committed outputs' : 'None'}</option>
        ${datasetRuns().map((r) => `<option value="${esc(r.id)}" ${r.id === S.runId ? 'selected' : ''}>
          ${esc(`${clock(r.started_at)} · ${r.cases.length} case${r.cases.length === 1 ? '' : 's'} · ${r.status}`)}</option>`).join('')}
      </select>
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
      <section class="col" id="input" aria-label="Input"></section>
      <section class="col bench" aria-label="Our solid">
        <header class="pane-head"><i class="sw ours"></i><h2>Ours</h2><span id="stage-label"></span><span class="spacer"></span><span id="steps-status"></span></header>
        ${viewportHTML('ours', 'Our solid. Drag to rotate, shift-drag to pan, scroll to zoom.')}
        <div class="scroll"><div id="steps-note"></div><ol id="steps"></ol><div id="step-detail"></div></div>
      </section>
      <section class="col bench" aria-label="Ground truth">
        <header class="pane-head"><i class="sw truth"></i><h2>${c.truth ? 'Ground truth' : 'Reference render'}</h2><span id="truth-label"></span></header>
        ${viewportHTML('truth', 'Ground truth solid')}
        <div class="scroll" id="metrics"></div>
      </section>`;
    renderInput(c);
    renderTruth(c);
  }
  renderSteps(c);
}

// -- left: input --------------------------------------------------------------------------------

function fmt(value) {
  if (value == null) return '–';
  if (typeof value === 'number') return num(value);
  if (typeof value !== 'object') return esc(value);
  if (Array.isArray(value)) {
    return value.map((v) => (Array.isArray(v) ? `(${v.map(fmt).join(', ')})` : fmt(v))).join(Array.isArray(value[0]) ? '<br>' : ', ') || '–';
  }
  return Object.entries(value).map(([k, v]) => `${fmt(v)} ${esc(words(k).toLowerCase())}`).join(' · ') || '–';
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
  const feature = ([key, value]) => {
    const title = `<h3>${esc(words(key))}</h3>`;
    if (!Array.isArray(value) || typeof value[0] !== 'object') return `${title}<p class="mono">${fmt(value)}</p>`;
    const columns = [...new Set(value.flatMap((item) => Object.keys(item)))];
    return `${title}<table class="grid"><thead><tr>${columns.map((k) => `<th>${esc(words(k))}</th>`).join('')}</tr></thead>
      <tbody>${value.map((item) => `<tr>${columns.map((k) => `<td>${fmt(item[k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  };
  return `<dl class="spec">${spec.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
    ${rest.length ? `<details class="fold"><summary>Features (${rest.length})</summary>${rest.map(feature).join('')}</details>` : ''}`;
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

function renderInput(c) {
  const images = [...c.views.map((file) => file.replace(/\.png$/, '')), ...(c.context ? ['context'] : [])];
  if (!images.includes(S.image)) S.image = images.includes(DEFAULT_VIEW) ? DEFAULT_VIEW : images[0];
  const ds = dataset();
  $('#input').innerHTML = `
    <header class="part">
      <h1>${esc(generic(c) ? `Case ${c.number}` : c.title)}</h1>
      <p>${esc([generic(c) ? '' : `Case ${c.number}`, ds.label, c.tier, envelope(c)].filter(Boolean).join(' · '))}</p>
    </header>
    ${images.length ? `<figure class="ref"><img id="big" alt="Reference render, ${esc(viewLabel(S.image))}" src="${imageSrc(c, S.image)}" data-cut="${imageSrc(c, S.image)}">
      <figcaption id="big-label">${esc(viewLabel(S.image))}</figcaption></figure>
    <div class="thumbs">${images.map((name, i) => `<button data-image="${name}" aria-pressed="${name === S.image}"
      title="${viewLabel(name)}${VIEWS[name] ? ` — turns the 3D views to this side (${i + 1})` : ''}">
      <img alt="${viewLabel(name)}" src="${imageSrc(c, name)}" data-cut="${imageSrc(c, name)}"></button>`).join('')}</div>` : '<p class="note">This case has no reference renders.</p>'}
    <h2>Description</h2><article class="prose clamp" id="brief"></article><button class="toggle" data-expand="brief">Show all</button>
    <h2>Facts</h2>${factsHTML(c.facts)}`;
  hydrateImages($('#input'));
  const id = live.id;
  fetch(`/api/description?dataset=${S.dataset}&case=${c.name}`).then((r) => r.text()).then((text) => {
    if (id === live.id) $('#brief').innerHTML = markdown(text);
  });
}

function showImage(name, turn = true) {
  const c = currentCase();
  if (!c || !$('#big')) return;
  S.image = name;
  $$('.thumbs button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.image === name));
  $$('#big, #truth img.ref').forEach((img) => { img.dataset.cut = imageSrc(c, name); });
  $('#big-label').textContent = viewLabel(name);
  if ($('#truth-label') && !c.truth) $('#truth-label').textContent = viewLabel(name);
  hydrateImages();
  if (turn && VIEWS[name]) setView(name);
}

// -- right: ground truth ------------------------------------------------------------------------

function renderTruth(c) {
  const id = live.id, output = outputOf(c);
  const state = { truth: undefined, ours: output ? undefined : null };
  const settle = (promise) => promise.then((data) => data.measured, () => null);
  if (c.truth) {
    const key = `${c.truth.url}?${c.truth.mtime}`;
    showModel($('#truth'), c.truth.url, key, COLORS.truth);
    settle(loadStep(c.truth.url, key)).then((m) => { state.truth = m; if (id === live.id) renderMetrics(c, state); });
  } else {
    state.truth = null;
    $('#truth .stage').innerHTML = `<img class="ref" alt="Reference render" src="${imageSrc(c, S.image)}" data-cut="${imageSrc(c, S.image)}">`;
    $('#truth-label').textContent = viewLabel(S.image);
    hydrateImages($('#truth'));
  }
  if (output) settle(loadStep(output.url, output.key)).then((m) => { state.ours = m; if (id === live.id) renderMetrics(c, state); });
  renderMetrics(c, state);
}

function delta(expected, actual) {
  if (expected == null || actual == null || !isFinite(expected) || !isFinite(actual) || !expected) return '<td></td>';
  const pct = (actual - expected) / expected * 100, abs = Math.abs(pct);
  const text = abs.toFixed(abs < 10 ? 1 : 0);
  return `<td class="${abs <= 1 ? 'good' : abs <= 5 ? 'warn' : 'bad'}">${Number(text) === 0 ? '±' : pct > 0 ? '+' : '−'}${text} %</td>`;
}

function renderMetrics(c, state) {
  const facts = c.facts || {}, box = facts.bounding_box_mm;
  const sorted = (dims) => (dims ? [...dims].sort((a, b) => b - a) : [null, null, null]);
  const expectedBox = sorted(box ? [box.x, box.y, box.z] : state.truth && state.truth.bbox);
  const oursBox = sorted(state.ours && state.ours.bbox);
  const rows = [
    ['Longest side', 'mm', expectedBox[0], oursBox[0]],
    ['Middle side', 'mm', expectedBox[1], oursBox[1]],
    ['Shortest side', 'mm', expectedBox[2], oursBox[2]],
    ['Volume', 'cm³', (facts.volume_mm3 ?? (state.truth && state.truth.volume)) / 1000, state.ours && state.ours.volume / 1000],
    ['Surface', 'cm²', (facts.surface_area_mm2 ?? (state.truth && state.truth.area)) / 100, state.ours && state.ours.area / 100],
  ];
  const cell = (value, unit) => (value == null || !isFinite(value) ? '<td class="none">–</td>' : `<td>${num(value)} <small>${unit}</small></td>`);
  const note = state.ours === undefined ? '<p class="note pulse">Measuring our output…</p>'
    : state.ours === null ? '<p class="note">No output of ours yet.</p>' : '';
  setHTML($('#metrics'), `<table class="metrics">
    <thead><tr><th></th><th><i class="sw truth"></i>Truth</th><th><i class="sw ours"></i>Ours</th><th>Diff</th></tr></thead>
    <tbody>${rows.map(([label, unit, expected, actual]) => `<tr><th>${label}</th>${cell(expected, unit)}${cell(actual, unit)}${delta(expected, actual)}</tr>`).join('')}</tbody>
  </table>${note}`);
}

// -- middle: steps ------------------------------------------------------------------------------

function buildTrace(events) {
  const t = { steps: new Map(), list: [], start: null, end: null, status: 'running', error: null, last: null };
  for (const e of events) {
    if (t.start == null) t.start = e.ts;
    t.last = e.ts;
    if (e.t === 'case_end') Object.assign(t, { end: e.ts, status: e.status, error: e.error });
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

const metricHTML = ([k, v]) => {
  const unit = (k.match(/_(cm3|mm3|mm2|mm)$/) || [])[1];
  return `${esc(words(unit ? k.slice(0, -unit.length - 1) : k))} <b>${esc(typeof v === 'number' ? num(v, 3) : v)}${unit ? ` ${unit.replace('3', '³').replace('2', '²')}` : ''}</b>`;
};
const raw = (value) => `<pre>${esc(typeof value === 'string' ? value : JSON.stringify(value, null, 2))}</pre>`;

function renderSteps(c) {
  if (!$('#steps') || !c) return;
  const run = currentRun(), output = outputOf(c);
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
      let model = null;
      t.list.forEach((s, index) => {
        const stage = s.artifacts.filter((a) => a.role === 'stage' && isStep(a.name)).pop();
        if (stage) model = { url: `${base}/${stage.path}` };
        const images = s.artifacts.filter((a) => isImage(a.name)), files = s.artifacts.filter((a) => !isImage(a.name));
        const value = (m) => metricHTML(m).split(' <b>')[1].replace('</b>', '');
        const text = [
          s.logs.length ? `<h4>Log</h4>${raw(s.logs.map((l) => l.msg).join('\n'))}` : '',
          s.input !== undefined ? `<h4>Input</h4>${raw(s.input)}` : '',
          s.output != null ? `<h4>Output</h4>${raw(s.output)}` : '',
          files.length ? `<p class="files">${files.map((a) => `<a href="${esc(`${base}/${a.path}`)}" target="_blank" rel="noopener">${esc(a.name)}</a>`).join('')}</p>` : '',
        ].join('');
        const more = [
          s.error ? `<pre class="bad">${esc(s.error)}</pre>` : '',
          `<div class="facts-row">${images.length ? `<div class="arts">${images.map((a) => `<button data-lightbox="${esc(`${base}/${a.path}`)}" title="${esc(a.name)}">
            <img loading="lazy" alt="${esc(a.name)}" src="${esc(`${base}/${a.path}`)}"></button>`).join('')}</div>` : ''}
          <dl class="kv">${Object.entries(s.metrics || {}).map((m) => `<dt>${metricHTML(m).split(' <b>')[0]}</dt><dd>${value(m)}</dd>`).join('')}
            <dt>time</dt><dd>${duration((s.end || (running ? now : t.last)) - s.start)}</dd></dl></div>`,
          text ? `<details class="fold"><summary>Log and data</summary>${text}</details>` : '',
        ].join('');
        rows.push({
          id: s.id, model, depth: s.depth, more, label: `after ${index + 1} · ${words(s.name)}`,
          state: s.end ? s.status : running ? 'running' : 'interrupted',
          n: String(index + 1).padStart(2, '0'), name: words(s.name),
          metric: Object.entries(s.metrics || {}).slice(0, 1).map(value).join(''),
          time: duration((s.end || (running ? now : t.last)) - s.start),
        });
      });
      if (!running) rows.forEach((r) => r.model && loadStep(r.model.url).catch(() => {}));  // warm up, so stepping is instant
    }
  } else if (!output) note = '<p class="note">No output yet. Run this case to see its steps.</p>';
  if (output) {
    rows.push({ id: 'output', model: output, depth: 0, label: 'final output', state: 'ok', n: '', name: 'Final', metric: '', time: '',
      more: `<p class="files"><a href="${esc(output.url)}" target="_blank" rel="noopener">Open .stp</a></p>` });
  }
  if (running && !rows.length) note = '<p class="note pulse">Running…</p>';

  // Which solid is on screen: the chosen step, else the newest one
  const chosen = rows.find((r) => r.id === S.sel) || [...rows].reverse().find((r) => r.model) || rows[rows.length - 1];
  live.rows = rows.map((r) => r.id);
  setHTML($('#steps-status'), status);
  setHTML($('#stage-label'), chosen && chosen.model ? esc(chosen.label) : '');
  setHTML($('#steps-note'), note);
  const strip = $('#steps'), cards = new Map($$('li', strip).map((li) => [li.dataset.id, li]));
  rows.forEach((r, i) => {
    let li = cards.get(r.id);
    cards.delete(r.id);
    if (!li) {
      li = document.createElement('li');
      li.dataset.id = r.id;
      li.innerHTML = '<button class="card"><span class="stage"></span><span class="cap"></span></button>';
    }
    if (strip.children[i] !== li) strip.insertBefore(li, strip.children[i] || null);
    const on = Boolean(chosen && r.id === chosen.id), button = li.firstElementChild;
    button.dataset.sel = r.id;
    button.setAttribute('aria-current', on);
    button.title = r.label;
    setHTML($('.cap', li), `<span class="n">${r.n}</span><span class="name">${esc(r.name)}</span>
      <span class="v ${r.state === 'ok' ? '' : `state ${r.state}`}">${r.state === 'ok' ? r.metric : r.state}</span>`);
    showModel(li, r.model && r.model.url, r.model && r.model.key, COLORS.ours, '', { interactive: false });
  });
  cards.forEach((li) => { const stage = $('.stage', li); if (stage.viewer) stage.viewer.dispose(); li.remove(); });
  setHTML($('#step-detail'), chosen && chosen.more ? `<h3>${esc(chosen.name)}</h3>${chosen.more}` : '');
  showModel($('#ours'), chosen && chosen.model && chosen.model.url, chosen && chosen.model && chosen.model.key, COLORS.ours,
    `<p class="note">${running ? 'Waiting for the first solid…' : 'No solid to show yet.'}</p>`);
}

function moveStep(offset) {
  const current = $('#steps [aria-current="true"]');
  const index = live.rows.indexOf(current ? current.dataset.sel : null);
  const next = live.rows[Math.max(0, Math.min(live.rows.length - 1, index + offset))];
  if (next == null) return;
  S.sel = next;
  renderSteps(currentCase());
  const card = $('#steps [aria-current="true"]');
  if (card) card.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

// -- overlay ------------------------------------------------------------------------------------

function closeOverlay() { $('#overlay').className = ''; $('#overlay').innerHTML = ''; }
function openOverlay(src) {
  $('#overlay').className = 'open';
  $('#overlay').innerHTML = `<img src="${esc(src)}" alt="">`;
}

// -- events -------------------------------------------------------------------------------------

function render() {
  normalise();
  writeHash();
  renderBar();
  renderMain();
}

function selectCase(name, datasetId = S.dataset) {
  if (datasetId !== S.dataset) Object.assign(S, { dataset: datasetId, runId: null });
  Object.assign(S, { caseName: name, sel: null });
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
  if (d.image) return showImage(d.image);
  if (d.zoom) return zoomBy(Number(d.zoom));
  if (d.fit !== undefined) return fit();
  if (d.lightbox) return openOverlay(d.lightbox);
  if (d.sel) { S.sel = d.sel; return renderSteps(currentCase()); }
  if (d.expand) { const closed = $(`#${d.expand}`).classList.toggle('clamp'); target.textContent = closed ? 'Show all' : 'Show less'; return; }
  if (d.action === 'run') {
    try {
      const { id } = await api('/api/run', { dataset: S.dataset, only: [S.caseName], target: 'run' });
      Object.assign(S, { runId: id, sel: null });
      await poll();
    } catch (error) { toast(`Could not start the run: ${error.message}`); }
    return;
  }
  if (d.action === 'stop') return api('/api/stop', { run: S.runId }).then(poll, (e) => toast(`Could not stop the run: ${e.message}`));
});

document.addEventListener('dblclick', (event) => {
  if (event.target.id === 'big') openOverlay(event.target.src);
});

document.addEventListener('change', (event) => {
  const el = event.target;
  if (el.id === 'case-select') { const [ds, name] = el.value.split('/'); selectCase(name, ds); }
  else if (el.id === 'run-select') { Object.assign(S, { runId: el.value, sel: null }); render(); }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') return closeOverlay();
  if (event.target.matches('input, select, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
  const act = {
    ArrowRight: () => moveCase(1), ArrowLeft: () => moveCase(-1),
    ArrowDown: () => moveStep(1), ArrowUp: () => moveStep(-1), ']': () => moveStep(1), '[': () => moveStep(-1),
    '+': () => zoomBy(1.25), '=': () => zoomBy(1.25), '-': () => zoomBy(0.8), f: fit, 0: fit,
  }[event.key] || (/^[1-8]$/.test(event.key) && (() => showImage(VIEW_NAMES[Number(event.key) - 1])));
  if (!act) return;
  event.preventDefault();
  act();
});

window.addEventListener('hashchange', async () => { readHash(); await ensureCases(); render(); });

// -- polling ------------------------------------------------------------------------------------

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await api('/api/state');
    const wasRunning = S.runs.filter((r) => r.status === 'running').map((r) => r.id);
    Object.assign(S, { datasets: state.datasets, runs: state.runs, now: state.now });
    normalise();
    await ensureCases(wasRunning.some((id) => (S.runs.find((r) => r.id === id) || {}).status !== 'running'));
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
  } finally { polling = false; }
}

readHash();
await poll();
setInterval(() => { if (!document.hidden) poll(); }, 2000);
