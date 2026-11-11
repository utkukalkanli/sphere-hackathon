// Orthographic 3D viewer whose named views match the cameras of the reference renders (README).
let libs = null;
async function load() {
  if (!libs) {
    libs = Promise.all([import('three'), import('three/addons/controls/TrackballControls.js')])
      .then(([THREE, controls]) => ({ THREE, TrackballControls: controls.TrackballControls }))
      .catch((error) => { libs = null; throw error; });
  }
  return libs;
}

// name -> [camera direction from the part's centre, up]
export const VIEWS = {
  view_0_front: [[0, -1, 0], [0, 0, 1]],
  view_1_back: [[0, 1, 0], [0, 0, 1]],
  view_2_left: [[-1, 0, 0], [0, 0, 1]],
  view_3_right: [[1, 0, 0], [0, 0, 1]],
  view_4_top: [[0, 0, 1], [0, 1, 0]],
  view_5_bottom: [[0, 0, -1], [0, -1, 0]],
  view_6_iso_front_right_top: [[1, -1, 1], [0, 0, 1]],
  view_7_iso_back_left_bottom: [[-1, 1, -1], [0, 0, 1]],
};

export const COLORS = { expected: 0xc79d63, ours: 0x5b8fc9 };

// layers: [{ meshes, color, opacity }]. With more than one layer (overlay), every layer is moved so
// that its bounding-box centre sits at the origin: outputs may be positioned anywhere.
export async function createViewer(container, layers) {
  const { THREE, TrackballControls } = await load();
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0xffffff);
  container.replaceChildren(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 1e6);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7f72, 1.1));
  const headlight = new THREE.DirectionalLight(0xffffff, 1.6);
  camera.add(headlight);
  headlight.position.set(-0.4, 0.8, 1);
  scene.add(camera);

  const root = new THREE.Group();
  const overlay = layers.length > 1;
  const groups = layers.map((layer) => {
    const translucent = layer.opacity != null && layer.opacity < 1;
    const material = new THREE.MeshStandardMaterial({
      color: layer.color ?? COLORS.expected, roughness: 0.75, metalness: 0, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      transparent: translucent, opacity: layer.opacity ?? 1, depthWrite: !translucent,
    });
    const lines = new THREE.LineBasicMaterial({
      color: translucent ? layer.color : 0x2b2118, transparent: translucent, opacity: translucent ? 0.9 : 1,
    });
    const group = new THREE.Group();
    let triangles = 0;
    for (const m of layer.meshes) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(m.position, 3));
      if (m.normal) geometry.setAttribute('normal', new THREE.BufferAttribute(m.normal, 3));
      geometry.setIndex(new THREE.BufferAttribute(m.index, 1));
      if (!m.normal) geometry.computeVertexNormals();
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = translucent ? 2 : 1;
      group.add(mesh);
      triangles += m.index.length / 3;
      if (triangles < 400000) group.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28), lines));
    }
    if (overlay) {
      const centre = new THREE.Box3().setFromObject(group).getCenter(new THREE.Vector3());
      group.position.sub(centre);
    }
    root.add(group);
    return group;
  });
  scene.add(root);

  const box = new THREE.Box3().setFromObject(root);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = sphere.radius || 1;
  const controls = new TrackballControls(camera, renderer.domElement);
  controls.staticMoving = true;
  controls.rotateSpeed = 3;
  controls.target.copy(sphere.center);

  function resize() {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    const half = radius * 1.12, aspect = w / h;
    camera.left = -half * Math.max(aspect, 1);
    camera.right = half * Math.max(aspect, 1);
    camera.top = half / Math.min(aspect, 1);
    camera.bottom = -half / Math.min(aspect, 1);
    camera.updateProjectionMatrix();
    controls.handleResize();
  }

  function setView(name) {
    const [direction, up] = VIEWS[name] || VIEWS.view_6_iso_front_right_top;
    controls.reset();
    controls.target.copy(sphere.center);
    camera.position.copy(sphere.center).add(new THREE.Vector3(...direction).normalize().multiplyScalar(radius * 4));
    camera.up.set(...up);
    camera.near = radius * 0.01;
    camera.far = radius * 10;
    camera.lookAt(sphere.center);
    camera.updateMatrixWorld();
    // Fill the frame like the reference renders do: zoom to the part's silhouette in this direction
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
    const top = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
    let halfX = 0, halfY = 0;
    const corner = new THREE.Vector3();
    for (let i = 0; i < 8; i++) {
      corner.set(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z)
        .sub(sphere.center);
      halfX = Math.max(halfX, Math.abs(corner.dot(right)));
      halfY = Math.max(halfY, Math.abs(corner.dot(top)));
    }
    camera.zoom = Math.min(camera.right / (halfX || 1), camera.top / (halfY || 1)) / 1.06;
    camera.updateProjectionMatrix();
  }

  // Camera pose relative to this model's own centre and size, so that two viewers showing
  // differently placed / sized solids can follow each other.
  function getPose() {
    return {
      eye: camera.position.clone().sub(controls.target).divideScalar(radius).toArray(),
      pan: controls.target.clone().sub(sphere.center).divideScalar(radius).toArray(),
      up: camera.up.toArray(),
      zoom: camera.zoom,
    };
  }

  function setPose(pose) {
    controls.target.copy(sphere.center).add(new THREE.Vector3(...pose.pan).multiplyScalar(radius));
    camera.position.copy(controls.target).add(new THREE.Vector3(...pose.eye).multiplyScalar(radius));
    camera.up.set(...pose.up);
    camera.zoom = pose.zoom;
    camera.lookAt(controls.target);
    camera.updateProjectionMatrix();
  }

  // Only report camera moves the user made in *this* viewer, not the ones applied by setPose
  let touched = 0, listener = null;
  const touch = () => { touched = performance.now(); };
  for (const type of ['pointerdown', 'pointermove', 'wheel']) {
    renderer.domElement.addEventListener(type, (e) => { if (type !== 'pointermove' || e.buttons) touch(); },
      { passive: true });
  }
  controls.addEventListener('change', () => {
    if (listener && performance.now() - touched < 400) listener(getPose());
  });

  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();
  setView('view_6_iso_front_right_top');

  let alive = true;
  (function frame() {
    if (!alive) return;
    if (!container.isConnected) return dispose();
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  })();

  function dispose() {
    if (!alive) return;
    alive = false;
    observer.disconnect();
    controls.dispose();
    root.traverse((o) => o.geometry && o.geometry.dispose());
    renderer.dispose();
    renderer.forceContextLoss();
  }
  return {
    setView, getPose, setPose, dispose,
    onChange(fn) { listener = fn; },
    setLayerVisible(index, visible) { if (groups[index]) groups[index].visible = visible; },
  };
}
