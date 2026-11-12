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
      renderer.localClippingEnabled = true;  // the section plane
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

// Every face of the solid in its own colour (C), to see where one face ends and the next begins
let facets = false;
try { facets = localStorage.getItem('faces') === 'on'; } catch { /* private mode */ }
export const faces = () => facets;
export function setFaces(on) {
  facets = Boolean(on);
  try { localStorage.setItem('faces', facets ? 'on' : 'off'); } catch { /* private mode */ }
  invalidate();
}
// Marking (M): a click on a solid, without dragging, is handed to the viewer's `onPick` with the place
// under the pointer, and the viewer's `pins` are drawn over the picture.
let picking = false;
export const marking = () => picking;
export function setMarking(on) {
  picking = Boolean(on);
  invalidate();
}
export { invalidate as redraw };

// The face of the solid a triangle belongs to, summed up from its triangles
function faceOf(m, triangle, before) {
  let f = -1;
  for (let i = 0; i < m.faces.length / 2; i++) if (m.faces[2 * i] <= triangle && triangle <= m.faces[2 * i + 1]) { f = i; break; }
  if (f < 0) return null;
  const p = m.position, min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity], sum = [0, 0, 0];
  let area = 0;
  for (let t = m.faces[2 * f]; t <= m.faces[2 * f + 1]; t++) {
    const [a, b, c] = [0, 1, 2].map((k) => m.index[3 * t + k] * 3);
    for (const v of [a, b, c]) for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], p[v + k]); max[k] = Math.max(max[k], p[v + k]); }
    const n = cross([p[b] - p[a], p[b + 1] - p[a + 1], p[b + 2] - p[a + 2]], [p[c] - p[a], p[c + 1] - p[a + 1], p[c + 2] - p[a + 2]]);
    area += Math.hypot(...n) / 2;
    for (let k = 0; k < 3; k++) sum[k] += n[k] / 2;
  }
  const flat = Math.hypot(...sum) > 0.9995 * area;
  return { index: before + f, area, flat, normal: flat ? unit(sum) : null, min, max };
}

// A section plane (X): every viewer shows its solid cut open square to one axis. `axis` is 0, 1, 2
// or -1 for none; `at` is where along the solid's box, 0 to 1. The half towards the camera is taken
// away, so the cut face is what you look at from either side.
const slice = { axis: -1, at: 0.5 };
export const section = () => slice;
export function setSection(axis, at = slice.at) {
  slice.axis = axis;
  slice.at = Math.min(1, Math.max(0, at));
  invalidate();
}

// Neighbouring numbers get hues far apart (golden-ratio steps round the colour wheel)
function faceColours(THREE, m) {
  const colours = new Float32Array(m.position.length).fill(0.7), colour = new THREE.Color();
  for (let f = 0; f < m.faces.length / 2; f++) {
    colour.setHSL((f * 0.618034) % 1, 0.5 + 0.12 * (f % 3), 0.52 + 0.07 * (f % 4));
    for (let t = m.faces[2 * f]; t <= m.faces[2 * f + 1]; t++) {
      for (let k = 0; k < 3; k++) colours.set([colour.r, colour.g, colour.b], m.index[3 * t + k] * 3);
    }
  }
  return new THREE.BufferAttribute(colours, 3);
}

// The outline of every face of the solid: the triangle edges that only one triangle of the face uses.
// Faces that meet smoothly get a line between them too, which an edge-by-angle test would miss.
function faceEdges(THREE, m) {
  const count = m.position.length / 3, points = [];
  for (let f = 0; f < m.faces.length / 2; f++) {
    const seen = new Map();
    for (let t = m.faces[2 * f]; t <= m.faces[2 * f + 1]; t++) {
      for (let k = 0; k < 3; k++) {
        const a = m.index[3 * t + k], b = m.index[3 * t + (k + 1) % 3], key = Math.min(a, b) * count + Math.max(a, b);
        seen.set(key, seen.has(key) ? -1 : a * count + b);
      }
    }
    for (const edge of seen.values()) {
      if (edge < 0) continue;
      const a = Math.floor(edge / count) * 3, b = (edge % count) * 3;
      points.push(m.position[a], m.position[a + 1], m.position[a + 2], m.position[b], m.position[b + 1], m.position[b + 2]);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(points), 3));
  return geometry;
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
    if (m.faces && m.faces.length) geometry.setAttribute('color', faceColours(THREE, m));
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
// steel = ours, brass = truth, blue = removed by the step, red = ours has it and the part does not,
// violet = the part has it and ours does not
export const COLORS = { ours: 0xb3bcc6, truth: 0xcfa462, ghost: 0x5b9bff, excess: 0xff5a4d, missing: 0xb98cff };

// Builds the viewer for `container` but leaves mounting `viewer.canvas` to the caller.
// `interactive: false` makes a thumbnail: it follows the shared camera but takes no input.
// `ghost`: the meshes of the solid before this step; what is no longer there shows as a shell.
// `frame`: { min, max } to frame instead of the solid itself, so every step sits in the same place.
// `diff`: { meshes, shift } of the ground truth (shift lays its min corner on the solid's). The solid
// is then painted red where it has material the truth lacks, and the truth shows in violet where it
// sticks out of the solid.
export async function createViewer(container, meshes, color = COLORS.ours, { interactive = true, ghost: before = null, frame = null, diff = null, pins = null, onPick = null } = {}) {
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
  const lines = new THREE.LineBasicMaterial({ color: 0x08090a });
  // The section. Where a solid is cut open the camera looks at the inside of its skin; exactly those
  // pixels are its cut face. They are painted flat and given the depth of the plane itself, so the
  // cut face hides and is hidden like a real face (and the diff below compares it like one).
  // cutDepth: the plane's depth at a pixel is dot((x, y, 1), cutDepth).
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 1e12), clip = [plane];
  const cut = { cutDepth: { value: new THREE.Vector3() }, cutOn: { value: 0 } };
  const capped = (target, cap, body = '', uniforms = {}) => {
    target.clippingPlanes = clip;
    target.customProgramCacheKey = () => `cut ${body}`;  // else three.js shares one program between them
    target.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, cut, uniforms, { capColour: { value: new THREE.Color(cap) } });
      shader.fragmentShader = `uniform vec3 cutDepth;\nuniform float cutOn;\nuniform vec3 capColour;\nuniform sampler2D otherDepth;\nuniform vec2 viewSize;\n${shader.fragmentShader}`.replace('#include <dithering_fragment>', `
        bool cap = cutOn > 0.5 && !gl_FrontFacing;
        float here = cap ? clamp(dot(vec3(gl_FragCoord.xy, 1.0), cutDepth), 0.0, 1.0) : gl_FragCoord.z;
        gl_FragDepth = here;
        float hatch = mod(gl_FragCoord.x + gl_FragCoord.y, 9.0) < 1.5 ? 0.74 : 1.0;  // cut faces are hatched
        if (cap) gl_FragColor.rgb = capColour * hatch;
        ${body}
        #include <dithering_fragment>`);
    };
  };
  // The outline of the cut faces: where the plane crosses the triangles. Everything left of a cut
  // solid lies behind the plane, so these lines are never hidden and need no depth test.
  const contour = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x000000, depthTest: false }));
  contour.renderOrder = 5;
  contour.frustumCulled = false;
  contour.visible = false;
  scene.add(contour);
  let traced = '';
  function trace(a, at) {
    if (traced === `${a} ${at}`) return;
    traced = `${a} ${at}`;
    const points = [];
    for (const [list, shift] of [[meshes, [0, 0, 0]], ...(diff ? [[diff.meshes, diff.shift]] : [])]) {
      for (const { position: p, index } of list) {
        const level = at - shift[a];
        for (let i = 0; i < index.length; i += 3) {
          const d0 = p[index[i] * 3 + a] - level, d1 = p[index[i + 1] * 3 + a] - level, d2 = p[index[i + 2] * 3 + a] - level;
          if ((d0 > 0) === (d1 > 0) && (d1 > 0) === (d2 > 0)) continue;
          for (const [u, v, du, dv] of [[index[i], index[i + 1], d0, d1], [index[i + 1], index[i + 2], d1, d2], [index[i + 2], index[i], d2, d0]]) {
            if ((du > 0) === (dv > 0)) continue;
            const t = du / (du - dv);
            for (let k = 0; k < 3; k++) points.push(p[u * 3 + k] + t * (p[v * 3 + k] - p[u * 3 + k]) + shift[k]);
          }
        }
      }
    }
    contour.geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  }
  const dim = (hex) => new THREE.Color(hex).multiplyScalar(0.6);
  capped(material, dim(color));
  lines.clippingPlanes = clip;
  const root = new THREE.Group(), shade = new THREE.Group(), used = [];
  let triangles = 0;
  const add = (group, list, fill, stroke, order) => {
    for (const m of list) {
      const entry = acquire(THREE, m);
      used.push(m);
      const mesh = new THREE.Mesh(entry.geometry, fill);
      mesh.userData.m = m;
      mesh.renderOrder = order;
      group.add(mesh);
      triangles += m.index.length / 3;
      if (triangles >= 400000) continue;
      entry.edges = entry.edges || (m.faces && m.faces.length ? faceEdges(THREE, m) : new THREE.EdgesGeometry(entry.geometry, 28));
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
  haze.clippingPlanes = hazeLines.clippingPlanes = clip;
  if (before && !diff) add(shade, before, haze, hazeLines, 0);
  scene.add(shade, root);

  // The difference to the ground truth, painted on the surfaces. The depth of each solid's nearest
  // surface is drawn into a texture first; then every pixel of our solid is compared with the truth
  // behind it and every pixel of the truth with our solid:
  //   our surface in front of the truth's: there is material under it the part does not have (red,
  //     stronger the thicker that layer is along the line of sight);
  //   the truth's surface in front of ours: the part sticks out of our solid, we lack it (violet);
  //   the two in the same place: our solid's own colour.
  let prepare = null;
  const extra = [];
  if (diff) {
    const size = new THREE.Vector2(1, 1), offset = { polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 };
    const depthOf = () => {
      const target = new THREE.WebGLRenderTarget(1, 1, { depthTexture: new THREE.DepthTexture(1, 1) });
      extra.push(target);
      return target;
    };
    const truthDepth = depthOf(), oursDepth = depthOf();
    // gap: how far the other solid's surface lies behind this pixel, in units of the depth range.
    // 4e-4 of it is about a quarter of a percent of the part's size: below that the two coincide.
    const compare = (target, cap, other, body) => capped(target, cap,
      `float gap = texture2D(otherDepth, gl_FragCoord.xy / viewSize).x - here;\n${body}`,
      { otherDepth: { value: other.depthTexture }, viewSize: { value: size } });
    compare(material, dim(color), truthDepth, `if (gap > 4e-4) {
      float light = dot(gl_FragColor.rgb, vec3(0.333));
      gl_FragColor.rgb = cap ? vec3(0.86, 0.2, 0.16) * hatch : mix(gl_FragColor.rgb, vec3(1.0, 0.27, 0.22) * (0.25 + light), 0.6 + 0.4 * clamp(gap * 40.0, 0.0, 1.0));
    }`);
    const lacking = new THREE.MeshStandardMaterial({ color: COLORS.missing, roughness: 0.7, metalness: 0, side: THREE.DoubleSide, ...offset });
    compare(lacking, dim(COLORS.missing).multiplyScalar(1.4), oursDepth, 'if (gap <= 4e-4) discard;');
    const plain = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, ...offset });
    capped(plain, 0);
    extra.push(lacking, plain);
    const truth = new THREE.Group();
    truth.position.set(...diff.shift);
    for (const m of diff.meshes) {
      used.push(m);
      truth.add(new THREE.Mesh(acquire(THREE, m).geometry, lacking));
    }
    scene.add(truth);
    const outlines = root.children.filter((o) => o.isLineSegments);
    prepare = (w, h) => {
      size.set(w, h);
      scene.overrideMaterial = plain;
      for (const o of outlines) o.visible = false;
      const cutting = contour.visible;
      contour.visible = false;
      for (const [target, shown, other] of [[truthDepth, truth, root], [oursDepth, root, truth]]) {
        if (target.width !== w || target.height !== h) target.setSize(w, h);
        shown.visible = true;
        other.visible = false;
        renderer.setRenderTarget(target);
        renderer.render(scene, camera);
      }
      renderer.setRenderTarget(null);
      scene.overrideMaterial = null;
      truth.visible = true;
      for (const o of outlines) o.visible = true;
      contour.visible = cutting;
    };
  }
  let centre, radius;
  const bounds = frame ? { min: frame.min, max: frame.max } : new THREE.Box3().setFromObject(root);
  const low = (i) => (frame ? bounds.min[i] : bounds.min.getComponent(i)), high = (i) => (frame ? bounds.max[i] : bounds.max.getComponent(i));
  const near = new THREE.Vector3(), far = new THREE.Vector3();
  // Puts the plane where the slider says and works out its depth across the image
  function place(w, h, dir) {
    const a = slice.axis;
    cut.cutOn.value = 0;
    contour.visible = a >= 0;
    if (a < 0) { plane.normal.set(0, 0, 1); plane.constant = 1e12; return; }
    const at = low(a) + slice.at * (high(a) - low(a)), side = dir[a] >= 0 ? -1 : 1;
    plane.normal.set(0, 0, 0).setComponent(a, side);
    plane.constant = -side * at;
    trace(a, at);
    if (Math.abs(dir[a]) < 1e-3) return;  // seen edge on: no cut face in sight
    camera.updateMatrixWorld();
    const depth = (x, y) => {
      near.set(x, y, -1).unproject(camera);
      far.set(x, y, 1).unproject(camera);
      return (at - near.getComponent(a)) / (far.getComponent(a) - near.getComponent(a));
    };
    const d = depth(-1, -1);
    cut.cutDepth.value.set((depth(1, -1) - d) / w, (depth(-1, 1) - d) / h, d);
    cut.cutOn.value = 1;
  }
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
    const coloured = facets && !diff;  // the diff paints the surfaces itself
    if (material.vertexColors !== coloured) {
      material.vertexColors = coloured;
      material.color.set(coloured ? 0xffffff : color);
      material.needsUpdate = true;
    }
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
    place(w, h, dir);
    if (prepare) prepare(w, h);
    renderer.render(scene, camera);
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(renderer.domElement, 0, renderer.domElement.height - h, w, h, 0, 0, w, h);
    if (interactive) drawAxes(dpr, h);
    if (pins) drawPins(dpr, w, h);
  }

  // The marks: a dot on the exact place, its number beside it. Fainter on a face turned away.
  const spot = new THREE.Vector3();
  const onScreen = (p, w, h) => { spot.set(...p).project(camera); return [(spot.x + 1) / 2 * w, (1 - spot.y) / 2 * h]; };
  function drawPins(dpr, w, h) {
    const { dir } = basis();
    ctx.font = `600 ${10 * dpr}px ui-monospace, Menlo, monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 1.5 * dpr;
    for (const pin of pins()) {
      const [x, y] = onScreen(pin.at, w, h), bx = x + 13 * dpr, by = y - 13 * dpr;
      ctx.globalAlpha = dot(pin.normal, dir) < -0.05 ? 0.45 : 1;
      ctx.strokeStyle = '#000';
      ctx.fillStyle = '#ffd24a';
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(bx, by); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 3.5 * dpr, 0, 7); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.arc(bx, by, 8.5 * dpr, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#000';
      ctx.fillText(pin.n, bx, by + 0.5 * dpr);
    }
    ctx.globalAlpha = 1;
  }

  // What lies under the pointer: a mark already there, else the nearest surface of the solid that
  // the section has not taken away. Where the camera looks into the cut solid, the place is on the
  // cut face, inside the material.
  const caster = new THREE.Raycaster();
  function pickAt(e) {
    const rect = canvas.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
    for (const pin of pins ? pins() : []) {
      const [px, py] = onScreen(pin.at, rect.width, rect.height);
      if (Math.hypot(px - x, py - y) < 8 || Math.hypot(px + 13 - x, py - 13 - y) < 10) return onPick({ pin });
    }
    const { dir, up, right } = basis(), ray = caster.ray;
    camera.updateMatrixWorld();
    ray.origin.set(x / rect.width * 2 - 1, 1 - y / rect.height * 2, -1).unproject(camera);
    ray.direction.set(...dir).negate();
    const solids = root.children.filter((o) => o.isMesh);
    const hit = caster.intersectObjects(solids, false).find((h) => plane.distanceToPoint(h.point) >= 0);
    if (!hit) return;
    const inside = hit.face.normal.dot(ray.direction) > 0, cutFace = inside && slice.axis >= 0;
    const point = cutFace ? ray.intersectPlane(plane, new THREE.Vector3()) : hit.point;
    if (!point) return;
    const normal = cutFace ? plane.normal.clone().negate() : inside ? hit.face.normal.clone().negate() : hit.face.normal;
    const m = hit.object.userData.m;
    const before = solids.slice(0, solids.indexOf(hit.object)).reduce((sum, o) => sum + o.userData.m.faces.length / 2, 0);
    onPick({
      kind: cutFace ? 'section' : 'surface', at: point.toArray(), normal: normal.toArray(),
      face: cutFace ? null : faceOf(m, hit.faceIndex, before), camera: { right, up, towards: dir },
    });
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
    drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, pan: e.shiftKey || e.button !== 0, flip, moved: false };
    canvas.classList.add('dragging');
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    if (!drag.moved) {  // while marking, a click that slips a pixel or two does not turn the part
      if (picking && onPick && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 4) return;
      drag.moved = true;
      drag.x = e.clientX;
      drag.y = e.clientY;
    }
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
  canvas.addEventListener('pointerup', (e) => {
    const click = drag && !drag.moved && !drag.pan && picking && onPick;
    release();
    if (click) pickAt(e);
  });
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  canvas.addEventListener('dblclick', () => { if (!(picking && onPick)) fit(); });
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
    for (const m of [material, lines, haze, hazeLines, contour.geometry, contour.material, ...extra]) m.dispose();
  }

  viewers.add(v);
  return v;
}
