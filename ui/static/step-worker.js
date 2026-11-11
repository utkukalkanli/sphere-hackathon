// Parses STEP files off the main thread (occt-import-js, OpenCascade compiled to WASM) and
// measures the tessellated solid: bounding box, volume, surface area, face count. It also estimates
// how much two solids overlap, by sampling points.
const CDN = 'https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/';
let occtPromise = null;

function measure(meshes) {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  let volume = 0, area = 0, triangles = 0;
  for (const { position: p, index } of meshes) {
    for (let i = 0; i < p.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        if (p[i + k] < min[k]) min[k] = p[i + k];
        if (p[i + k] > max[k]) max[k] = p[i + k];
      }
    }
    for (let i = 0; i < index.length; i += 3) {
      const a = index[i] * 3, b = index[i + 1] * 3, c = index[i + 2] * 3;
      const ax = p[a], ay = p[a + 1], az = p[a + 2];
      const bx = p[b], by = p[b + 1], bz = p[b + 2];
      const cx = p[c], cy = p[c + 1], cz = p[c + 2];
      const nx = (by - ay) * (cz - az) - (bz - az) * (cy - ay);
      const ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az);
      const nz = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
      area += Math.sqrt(nx * nx + ny * ny + nz * nz) / 2;
      volume += (ax * nx + ay * ny + az * nz) / 6;  // signed tetrahedron against the origin
      triangles++;
    }
  }
  return { bbox: max.map((v, k) => v - min[k]), min, max, volume: Math.abs(volume), area, triangles };
}

// A test "is this point inside the solid", for points given from the solid's min corner. A ray along
// +x is counted through the triangles; these are binned on a grid over y and z so each point only
// meets the few triangles above its own cell.
function tester({ meshes, min, max }) {
  const G = 128, sy = G / ((max[1] - min[1]) || 1), sz = G / ((max[2] - min[2]) || 1);
  let count = 0;
  for (const m of meshes) count += m.index.length / 3;
  const T = new Float32Array(count * 9);
  let t = 0;
  for (const { position: p, index } of meshes) {
    for (let i = 0; i < index.length; i++) {
      const v = index[i] * 3;
      T[t++] = p[v] - min[0]; T[t++] = p[v + 1] - min[1]; T[t++] = p[v + 2] - min[2];
    }
  }
  const cell = (value, scale) => Math.max(0, Math.min(G - 1, Math.floor(value * scale)));
  const span = (i) => {
    const o = i * 9;
    return [cell(Math.min(T[o + 1], T[o + 4], T[o + 7]), sy), cell(Math.max(T[o + 1], T[o + 4], T[o + 7]), sy),
      cell(Math.min(T[o + 2], T[o + 5], T[o + 8]), sz), cell(Math.max(T[o + 2], T[o + 5], T[o + 8]), sz)];
  };
  const start = new Uint32Array(G * G + 1);
  for (let i = 0; i < count; i++) {
    const [y0, y1, z0, z1] = span(i);
    for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) start[y * G + z + 1]++;
  }
  for (let i = 0; i < G * G; i++) start[i + 1] += start[i];
  const fill = start.slice(0, G * G), list = new Uint32Array(start[G * G]);
  for (let i = 0; i < count; i++) {
    const [y0, y1, z0, z1] = span(i);
    for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) list[fill[y * G + z]++] = i;
  }
  const height = max[1] - min[1], depth = max[2] - min[2];
  return (px, py, pz) => {
    if (py < 0 || pz < 0 || py > height || pz > depth) return false;
    const c = cell(py, sy) * G + cell(pz, sz);
    let crossings = 0;
    for (let k = start[c]; k < start[c + 1]; k++) {
      const o = list[k] * 9;
      const ay = T[o + 1], az = T[o + 2], e1y = T[o + 4] - ay, e1z = T[o + 5] - az, e2y = T[o + 7] - ay, e2z = T[o + 8] - az;
      const d = e1y * e2z - e1z * e2y;
      if (Math.abs(d) < 1e-12) continue;
      const u = ((py - ay) * e2z - (pz - az) * e2y) / d, v = (e1y * (pz - az) - e1z * (py - ay)) / d;
      if (u < 0 || v < 0 || u + v > 1) continue;
      if (T[o] + u * (T[o + 3] - T[o]) + v * (T[o + 6] - T[o]) > px) crossings++;
    }
    return crossings % 2 === 1;
  };
}

// Overlap of two solids with their min corners laid on each other (ours is built at the origin, the
// ground truth is not): intersection over union, and the volume only one of them has.
function overlap(a, b) {
  const inA = tester(a), inB = tester(b);
  const size = [0, 1, 2].map((k) => Math.max(a.max[k] - a.min[k], b.max[k] - b.min[k]));
  let seed = 12345;
  const random = () => {  // fixed sequence, so the same two solids always give the same number
    seed = (seed + 0x6D2B79F5) | 0;
    let r = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
  const N = 40000;
  let onlyA = 0, onlyB = 0, both = 0;
  for (let i = 0; i < N; i++) {
    const x = random() * size[0], y = random() * size[1], z = random() * size[2];
    const ia = inA(x, y, z), ib = inB(x, y, z);
    if (ia && ib) both++; else if (ia) onlyA++; else if (ib) onlyB++;
  }
  const unit = size[0] * size[1] * size[2] / N;
  return { iou: both / ((both + onlyA + onlyB) || 1), onlyA: onlyA * unit, onlyB: onlyB * unit, points: N };
}

onmessage = async ({ data: { id, buffer, compare } }) => {
  if (compare) {
    try { postMessage({ id, ok: true, overlap: overlap(compare.a, compare.b) }); }
    catch (error) { postMessage({ id, ok: false, error: String(error && error.message || error) }); }
    return;
  }
  try {
    if (!occtPromise) {
      importScripts(CDN + 'occt-import-js.js');
      occtPromise = occtimportjs({ locateFile: (file) => CDN + file });
    }
    const occt = await occtPromise;
    const result = occt.ReadStepFile(new Uint8Array(buffer), {
      linearUnit: 'millimeter', linearDeflectionType: 'bounding_box_ratio', linearDeflection: 0.0005,
      angularDeflection: 0.2,
    });
    if (!result || !result.success || !result.meshes.length) throw new Error('not a readable STEP solid');
    const meshes = result.meshes.map((m) => ({
      position: new Float32Array(m.attributes.position.array),
      normal: m.attributes.normal ? new Float32Array(m.attributes.normal.array) : null,
      index: new Uint32Array(m.index.array),
    }));
    const faces = result.meshes.reduce((sum, m) => sum + (m.brep_faces ? m.brep_faces.length : 0), 0);
    const transfer = meshes.flatMap((m) => [m.position.buffer, m.index.buffer, ...(m.normal ? [m.normal.buffer] : [])]);
    const measured = { ...measure(meshes), faces: faces || null };
    postMessage({ id, ok: true, meshes, measured }, transfer);
  } catch (error) {
    occtPromise = occtPromise && null;
    postMessage({ id, ok: false, error: String(error && error.message || error) });
  }
};
