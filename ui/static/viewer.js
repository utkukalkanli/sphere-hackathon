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

const pose = { az: 45, el: ISO, zoom: 1, pan: [0, 0, 0] };  // pan in units of each model's radius
const viewers = new Set();
let frame = 0, tween = 0;

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
  const turn = ((to.az - from.az) % 360 + 540) % 360 - 180;  // the short way round
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const start = performance.now();
  const step = (now) => {
    const t = still ? 1 : Math.min(1, (now - start) / 280), k = 1 - (1 - t) ** 4;
    pose.az = from.az + turn * k;
    pose.el = from.el + (to.el - from.el) * k;
    pose.zoom = from.zoom + (to.zoom - from.zoom) * k;
    pose.pan = from.pan.map((v, i) => v + (to.pan[i] - v) * k);
    invalidate();
    if (t < 1) tween = requestAnimationFrame(step);
  };
  tween = requestAnimationFrame(step);
}

export function setView(name) {
  const [az, el] = VIEWS[name] || VIEWS.view_6_iso_front_right_top;
  glide({ az, el, zoom: 1, pan: [0, 0, 0] });
}
export function fit() {
  glide({ az: pose.az, el: pose.el, zoom: 1, pan: [0, 0, 0] });
}
export function zoomBy(factor) {
  pose.zoom = Math.min(60, Math.max(0.2, pose.zoom * factor));
  invalidate();
}

function basis() {
  const a = pose.az * Math.PI / 180, e = pose.el * Math.PI / 180;
  return {
    dir: [Math.sin(a) * Math.cos(e), -Math.cos(a) * Math.cos(e), Math.sin(e)],  // from the part to the camera
    up: [-Math.sin(a) * Math.sin(e), Math.cos(a) * Math.sin(e), Math.cos(e)],
    right: [Math.cos(a), Math.sin(a), 0],
  };
}

const AXES = [['x', [1, 0, 0], '#ff7a6b'], ['y', [0, 1, 0], '#6fcf97'], ['z', [0, 0, 1], '#6aa5ff']];
export const COLORS = { ours: 0xb3bcc6, truth: 0xcfa462 };  // steel = ours, brass = the reference renders

// Builds the viewer for `container` but leaves mounting `viewer.canvas` to the caller.
// `interactive: false` makes a thumbnail: it follows the shared camera but takes no input.
export async function createViewer(container, meshes, color = COLORS.ours, { interactive = true } = {}) {
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
  const root = new THREE.Group();
  let triangles = 0;
  for (const m of meshes) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(m.position, 3));
    if (m.normal) geometry.setAttribute('normal', new THREE.BufferAttribute(m.normal, 3));
    geometry.setIndex(new THREE.BufferAttribute(m.index, 1));
    if (!m.normal) geometry.computeVertexNormals();
    root.add(new THREE.Mesh(geometry, material));
    triangles += m.index.length / 3;
    if (triangles < 400000) root.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28), lines));
  }
  scene.add(root);
  const sphere = new THREE.Box3().setFromObject(root).getBoundingSphere(new THREE.Sphere());
  const centre = sphere.center, radius = sphere.radius || 1;
  const target = new THREE.Vector3(), bufferSize = new THREE.Vector2();

  const v = { canvas, dirty: true, visible: false, render, dispose };

  function render() {
    v.dirty = false;
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
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
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
    drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button !== 0 };
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
      pose.az -= dx * 0.5;
      pose.el = Math.max(-90, Math.min(90, pose.el + dy * 0.5));
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
    root.traverse((o) => o.geometry && o.geometry.dispose());
    material.dispose();
    lines.dispose();
  }

  viewers.add(v);
  return v;
}
