// Orthographic 3D viewers that all share ONE camera pose: rotating any of them rotates every one.
// Turntable controls (Z stays up, no roll), so the part can't end up tumbled:
//   drag = rotate · shift-drag / right-drag = pan · scroll or pinch = zoom · double-click = fit
// A single WebGL context renders every viewer and the result is copied to a 2D canvas, so any number
// of viewers can be on the page. Frames are only drawn when something changed.
let libs = null;
function load() {
  if (!libs) {
    libs = import('three').then((THREE) => {
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(1);
      renderer.setScissorTest(true);
      return { THREE, renderer };
    }).catch((error) => { libs = null; throw error; });
  }
  return libs;
}

const ISO = Math.atan(1 / Math.SQRT2) * 180 / Math.PI;
// name -> [azimuth, elevation] in degrees; matches the cameras of the reference renders (README)
export const VIEWS = {
  view_0_front: [0, 0],
  view_1_back: [180, 0],
  view_2_left: [-90, 0],
  view_3_right: [90, 0],
  view_4_top: [0, 90],
  view_5_bottom: [0, -90],
  view_6_iso_front_right_top: [45, ISO],
  view_7_iso_back_left_bottom: [-135, -ISO],
};

// The camera is two unit vectors in model space: screen right and screen up
const RAD = Math.PI / 180;
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (a) => { const n = Math.hypot(...a) || 1; return a.map((x) => x / n); };
function spin(v, axis, angle) {  // v turned about a unit axis, angle in radians
  const c = Math.cos(angle), s = Math.sin(angle), x = cross(axis, v), d = dot(axis, v) * (1 - c);
  return v.map((value, i) => value * c + x[i] * s + axis[i] * d);
}
function fromAngles(az, el) {
  const a = az * RAD, e = el * RAD;
  return { right: [Math.cos(a), Math.sin(a), 0], up: [-Math.sin(a) * Math.sin(e), Math.cos(a) * Math.sin(e), Math.cos(e)] };
}
// Axis and angle of the one turn that takes camera `a` to camera `b`
function turnBetween(a, b) {
  const A = [a.right, a.up, cross(a.right, a.up)], B = [b.right, b.up, cross(b.right, b.up)];
  const R = [0, 1, 2].map((r) => [0, 1, 2].map((c) => B[0][r] * A[0][c] + B[1][r] * A[1][c] + B[2][r] * A[2][c]));
  const cos = Math.max(-1, Math.min(1, (R[0][0] + R[1][1] + R[2][2] - 1) / 2));
  const skew = [R[2][1] - R[1][2], R[0][2] - R[2][0], R[1][0] - R[0][1]], sin = Math.hypot(...skew) / 2;
  if (sin > 1e-6) return { axis: unit(skew), angle: Math.atan2(sin, cos) };
  if (cos > 0) return { axis: [0, 0, 1], angle: 0 };
  const i = [0, 1, 2].reduce((best, k) => (R[k][k] > R[best][best] ? k : best), 0);  // half a turn
  const ai = Math.sqrt((R[i][i] + 1) / 2);
  return { axis: unit([0, 1, 2].map((j) => (j === i ? ai : (R[i][j] + R[j][i]) / (4 * ai)))), angle: Math.PI };
}

const pose = { ...fromAngles(45, ISO), zoom: 1, pan: [0, 0, 0] };  // pan in units of each model's radius
const viewers = new Set();
let frame = 0, tween = 0;

function basis() {
  return { right: pose.right, up: pose.up, dir: cross(pose.right, pose.up) };  // dir: from the part to the camera
}

// Drag mode. Turntable: sideways turns about the part's Z. Trackball: about the screen's own axes.
let free = false;
try { free = localStorage.getItem('orbit') === 'trackball'; } catch { /* private mode */ }
export const trackball = () => free;
export function setTrackball(on) {
  free = Boolean(on);
  try { localStorage.setItem('orbit', free ? 'trackball' : 'turntable'); } catch { /* private mode */ }
}

function schedule() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    for (const v of viewers) if (v.dirty && v.visible) v.render();
  });
}
function invalidate() {
  for (const v of viewers) v.dirty = true;
  schedule();
}

// Glide to a pose instead of jumping, so you can see which way the part turned
function glide(to) {
  cancelAnimationFrame(tween);
  const from = { ...pose, pan: [...pose.pan] };
  const { axis, angle } = turnBetween(from, to);
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const start = performance.now();
  const step = (now) => {
    const t = still ? 1 : Math.min(1, (now - start) / 280), k = 1 - (1 - t) ** 4;
    pose.right = t < 1 ? spin(from.right, axis, angle * k) : to.right;
    pose.up = t < 1 ? spin(from.up, axis, angle * k) : to.up;
    pose.zoom = from.zoom + (to.zoom - from.zoom) * k;
    pose.pan = from.pan.map((v, i) => v + (to.pan[i] - v) * k);
    invalidate();
    if (t < 1) tween = requestAnimationFrame(step);
  };
  tween = requestAnimationFrame(step);
}

export function setView(name) {
  const [az, el] = VIEWS[name] || VIEWS.view_6_iso_front_right_top;
  glide({ ...fromAngles(az, el), zoom: 1, pan: [0, 0, 0] });
}
export function fit() {
  glide({ right: pose.right, up: pose.up, zoom: 1, pan: [0, 0, 0] });
}
export function zoomBy(factor) {
  pose.zoom = Math.min(60, Math.max(0.2, pose.zoom * factor));
  invalidate();
}

// The material a step removed is drawn as a translucent shell over the solid; G switches it off
let ghosts = true;
try { ghosts = localStorage.getItem('ghost') !== 'off'; } catch { /* private mode */ }
export const ghost = () => ghosts;
export function setGhost(on) {
  ghosts = Boolean(on);
  try { localStorage.setItem('ghost', ghosts ? 'on' : 'off'); } catch { /* private mode */ }
  invalidate();
}

// Geometry is built once per loaded mesh and shared by every viewer that shows it, so scrubbing
// through the steps does not rebuild it. It is freed a while after the last viewer let go.
const built = new Map();
function acquire(THREE, m) {
  let entry = built.get(m);
  if (!entry) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(m.position, 3));
    if (m.normal) geometry.setAttribute('normal', new THREE.BufferAttribute(m.normal, 3));
    geometry.setIndex(new THREE.BufferAttribute(m.index, 1));
    if (!m.normal) geometry.computeVertexNormals();
    entry = { geometry, edges: null, uses: 0, timer: 0 };
    built.set(m, entry);
  }
  clearTimeout(entry.timer);
  entry.uses++;
  return entry;
}
function release(m) {
  const entry = built.get(m);
  if (!entry || --entry.uses > 0) return;
  entry.timer = setTimeout(() => {
    entry.geometry.dispose();
    if (entry.edges) entry.edges.dispose();
    built.delete(m);
  }, 30000);
}

const AXES = [['x', [1, 0, 0], '#ff7a6b'], ['y', [0, 1, 0], '#6fcf97'], ['z', [0, 0, 1], '#6aa5ff']];
export const COLORS = { ours: 0xb3bcc6, truth: 0xcfa462, ghost: 0x5b9bff };  // steel = ours, brass = truth, blue = removed

// Builds the viewer for `container` but leaves mounting `viewer.canvas` to the caller.
// `interactive: false` makes a thumbnail: it follows the shared camera but takes no input.
// `ghost`: the meshes of the solid before this step; what is no longer there shows as a shell.
// `frame`: { min, max } to frame instead of the solid itself, so every step sits in the same place.
export async function createViewer(container, meshes, color = COLORS.ours, { interactive = true, ghost: before = null, frame = null } = {}) {
  const { THREE, renderer } = await load();
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 1e6);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2f36, 1.0));
  const key = new THREE.DirectionalLight(0xffffff, 1.7);
  key.position.set(-0.5, 0.9, 1);
  const fill = new THREE.DirectionalLight(0xffffff, 0.45);
  fill.position.set(1, -0.3, 0.6);
  camera.add(key, fill);
  scene.add(camera);

  const material = new THREE.MeshStandardMaterial({
    color, roughness: 0.62, metalness: 0.12, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
  });
  const lines = new THREE.LineBasicMaterial({ color: 0x0b0d10 });
  const root = new THREE.Group(), shade = new THREE.Group(), used = [];
  let triangles = 0;
  const add = (group, list, fill, stroke, order) => {
    for (const m of list) {
      const entry = acquire(THREE, m);
      used.push(m);
      const mesh = new THREE.Mesh(entry.geometry, fill);
      mesh.renderOrder = order;
      group.add(mesh);
      triangles += m.index.length / 3;
      if (triangles >= 400000) continue;
      entry.edges = entry.edges || new THREE.EdgesGeometry(entry.geometry, 28);
      const edges = new THREE.LineSegments(entry.edges, stroke);
      edges.renderOrder = order;
      group.add(edges);
    }
  };
  add(root, meshes, material, lines, 1);
  // Pushed far back in depth, so where the old solid and the new one share a face the new one wins
  // and only what was removed shows.
  const haze = new THREE.MeshBasicMaterial({
    color: COLORS.ghost, transparent: true, opacity: 0.13, depthWrite: false, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: 4, polygonOffsetUnits: 3000,
  });
  const hazeLines = new THREE.LineBasicMaterial({ color: 0x3f73c4 });
  if (before) add(shade, before, haze, hazeLines, 0);
  scene.add(shade, root);
  let centre, radius;
  if (frame) {
    centre = new THREE.Vector3(...frame.min.map((v, i) => (v + frame.max[i]) / 2));
    radius = Math.hypot(...frame.min.map((v, i) => frame.max[i] - v)) / 2 || 1;
  } else {
    const sphere = new THREE.Box3().setFromObject(root).getBoundingSphere(new THREE.Sphere());
    centre = sphere.center;
    radius = sphere.radius || 1;
  }
  const target = new THREE.Vector3(), bufferSize = new THREE.Vector2();

  const v = { canvas, dirty: true, visible: false, render, dispose };

  function render() {
    v.dirty = false;
    shade.visible = ghosts;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(container.clientWidth * dpr), h = Math.round(container.clientHeight * dpr);
    if (w < 2 || h < 2) return;
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    const { dir, up } = basis();
    const half = radius * 1.1, aspect = w / h;
    camera.left = -half * Math.max(aspect, 1);
    camera.right = half * Math.max(aspect, 1);
    camera.top = half / Math.min(aspect, 1);
    camera.bottom = -half / Math.min(aspect, 1);
    camera.zoom = pose.zoom;
    camera.near = radius * 0.01;
    camera.far = radius * 12;
    target.set(...pose.pan).multiplyScalar(radius).add(centre);
    camera.position.set(...dir).multiplyScalar(radius * 6).add(target);
    camera.up.set(...up);
    camera.lookAt(target);
    camera.updateProjectionMatrix();

    renderer.getSize(bufferSize);
    if (bufferSize.x < w || bufferSize.y < h) renderer.setSize(Math.max(bufferSize.x, w), Math.max(bufferSize.y, h), false);
    renderer.setViewport(0, 0, w, h);
    renderer.setScissor(0, 0, w, h);
    renderer.render(scene, camera);
    const glow = ctx.createRadialGradient(w / 2, h * 0.45, 0, w / 2, h * 0.45, Math.max(w, h) * 0.7);
    glow.addColorStop(0, '#20252b');
    glow.addColorStop(1, '#0c0e10');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(renderer.domElement, 0, renderer.domElement.height - h, w, h, 0, 0, w, h);
    if (interactive) drawAxes(dpr, h);
  }

  function drawAxes(dpr, h) {  // which way the part is turned
    const { dir, up, right } = basis();
    const ox = 26 * dpr, oy = h - 26 * dpr, length = 15 * dpr;
    ctx.lineWidth = 1.5 * dpr;
    ctx.font = `${10 * dpr}px ui-monospace, Menlo, monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const [label, axis, colour] of [...AXES].sort((a, b) => dot(a[1], dir) - dot(b[1], dir))) {
      const x = dot(axis, right), y = -dot(axis, up);
      ctx.strokeStyle = ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.moveTo(ox, oy);
      ctx.lineTo(ox + x * length, oy + y * length);
      ctx.stroke();
      ctx.fillText(label, ox + x * (length + 6 * dpr), oy + y * (length + 6 * dpr));
    }
  }

  // model units (in radii) per CSS pixel at the current zoom
  const scale = () => 2 * (camera.top / radius) / ((container.clientHeight || 1) * pose.zoom);
  const move = (vector, amount) => { for (let i = 0; i < 3; i++) pose.pan[i] += vector[i] * amount; };

  if (interactive) {
  let drag = null;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    cancelAnimationFrame(tween);
    // past the pole the part is upside down: turn the other way so it still follows the pointer
    const flip = pose.up[2] < 0 ? -1 : 1;
    drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button !== 0, flip };
    canvas.classList.add('dragging');
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX;
    drag.y = e.clientY;
    if (drag.pan) {
      const { up, right } = basis(), k = scale();
      move(right, -dx * k);
      move(up, dy * k);
    } else {
      // no stop at the poles: the part tumbles all the way round
      const about = free ? pose.up : [0, 0, 1], turn = -dx * 0.5 * RAD * (free ? 1 : drag.flip);
      const right = unit(spin(pose.right, about, turn));
      const up = spin(spin(pose.up, about, turn), right, -dy * 0.5 * RAD);
      pose.right = right;
      pose.up = unit(up.map((value, i) => value - right[i] * dot(up, right)));  // keep them square
    }
    invalidate();
  });
  const release = () => { drag = null; canvas.classList.remove('dragging'); };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  canvas.addEventListener('dblclick', fit);
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    cancelAnimationFrame(tween);
    const before = pose.zoom;
    const next = Math.min(60, Math.max(0.2, before * Math.exp(-e.deltaY * (e.ctrlKey ? 0.012 : 0.0018))));
    const rect = canvas.getBoundingClientRect(), k = scale();
    const cx = e.clientX - rect.left - rect.width / 2, cy = rect.height / 2 - (e.clientY - rect.top);
    const { up, right } = basis(), keep = 1 - before / next;  // keep the point under the cursor in place
    move(right, cx * k * keep);
    move(up, cy * k * keep);
    pose.zoom = next;
    invalidate();
  }, { passive: false });
  }

  const resizes = new ResizeObserver(() => { v.dirty = true; schedule(); });
  resizes.observe(container);
  const sight = new IntersectionObserver(([entry]) => {
    v.visible = entry.isIntersecting;
    if (v.visible && v.dirty) schedule();
  });
  sight.observe(container);

  function dispose() {
    if (!viewers.delete(v)) return;
    resizes.disconnect();
    sight.disconnect();
    used.forEach(release);
    for (const m of [material, lines, haze, hazeLines]) m.dispose();
  }

  viewers.add(v);
  return v;
}
