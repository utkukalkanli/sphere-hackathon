// Parses STEP files off the main thread (occt-import-js, OpenCascade compiled to WASM) and
// measures the tessellated solid: bounding box, volume, surface area.
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

onmessage = async ({ data: { id, buffer } }) => {
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
    const transfer = meshes.flatMap((m) => [m.position.buffer, m.index.buffer, ...(m.normal ? [m.normal.buffer] : [])]);
    postMessage({ id, ok: true, meshes, measured: measure(meshes) }, transfer);
  } catch (error) {
    occtPromise = occtPromise && null;
    postMessage({ id, ok: false, error: String(error && error.message || error) });
  }
};
