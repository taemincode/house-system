import * as THREE from 'three';
import { OBJLoader } from './vendor/three/OBJLoader.js';
import { MTLLoader } from './vendor/three/MTLLoader.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

async function showTrophy(container) {
  const status = container.querySelector('.trophy-model-status');
  let renderer, controls, observer, object;
  const fail = () => {
    status.hidden = false;
    status.textContent = 'The 3D preview could not load. Please refresh to try again.';
    container.dataset.state = 'error';
  };

  try {
    container.dataset.state = 'loading';
    const manager = new THREE.LoadingManager();
    // Texture paths in the MTL resolve relative to that file's folder.
    const materials = await new MTLLoader(manager).loadAsync(container.dataset.mtl);
    const resourcesReady = new Promise((resolve, reject) => {
      manager.onLoad = resolve;
      manager.onError = (url) => reject(new Error(`Unable to load trophy asset: ${url}`));
    });
    [object] = await Promise.all([
      new OBJLoader(manager).setMaterials(materials).loadAsync(container.dataset.obj),
      resourcesReady,
    ]);
    // Tinkercad OBJ exports are Z-up; the web scene is Y-up.
    if (container.dataset.up === 'z') object.rotation.x = -Math.PI / 2;
    const bounds = new THREE.Box3().setFromObject(object);
    const size = bounds.getSize(new THREE.Vector3());
    const longest = Math.max(size.x, size.y, size.z);
    if (!Number.isFinite(longest) || longest <= 0) throw new Error('Empty trophy');
    object.position.sub(bounds.getCenter(new THREE.Vector3()));
    const normalized = new THREE.Group();
    normalized.add(object);
    normalized.scale.setScalar(2 / longest);
    const radius = new THREE.Box3().setFromObject(normalized).getBoundingSphere(new THREE.Sphere()).radius;

    const scene = new THREE.Scene();
    scene.add(normalized, new THREE.HemisphereLight(0xffffff, 0x777777, 2));
    const key = new THREE.DirectionalLight(0xffffff, 2.5);
    key.position.set(3, 5, 5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 1);
    fill.position.set(-4, 2, -2);
    scene.add(fill);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    const canvas = renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `${container.dataset.label}. Drag or use arrow keys to rotate; plus and minus to zoom.`);
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
    controls = new OrbitControls(camera, canvas);
    controls.enablePan = false;
    controls.minDistance = 1.5;
    controls.maxDistance = 12;
    controls.maxPolarAngle = Math.PI * .95;
    const render = () => renderer.render(scene, camera);
    controls.addEventListener('change', render);
    // Render only on interaction or resize, rather than running an idle loop.
    const resize = () => {
      const width = container.clientWidth, height = container.clientHeight;
      if (!width || !height) return;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      const halfFov = Math.atan(Math.tan(THREE.MathUtils.degToRad(17.5)) * Math.min(camera.aspect, 1));
      const distance = radius * .85 / Math.sin(halfFov);
      camera.position.set(0, Math.sin(Math.PI / 7) * distance, Math.cos(Math.PI / 7) * distance);
      controls.update();
      renderer.setSize(width, height, false);
      render();
    };
    canvas.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-'].includes(event.key)) return;
      event.preventDefault();
      const orbit = new THREE.Spherical().setFromVector3(camera.position);
      if (event.key === 'ArrowLeft') orbit.theta -= .12;
      if (event.key === 'ArrowRight') orbit.theta += .12;
      if (event.key === 'ArrowUp') orbit.phi -= .12;
      if (event.key === 'ArrowDown') orbit.phi += .12;
      if (event.key === '+' || event.key === '=') orbit.radius *= .9;
      if (event.key === '-') orbit.radius *= 1.1;
      orbit.phi = THREE.MathUtils.clamp(orbit.phi, .05, controls.maxPolarAngle);
      orbit.radius = THREE.MathUtils.clamp(orbit.radius, controls.minDistance, controls.maxDistance);
      camera.position.setFromSpherical(orbit);
      controls.update();
    });
    container.append(canvas);
    status.hidden = true;
    container.dataset.state = 'ready';
    observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
  } catch (error) {
    observer?.disconnect();
    controls?.dispose();
    renderer?.dispose();
    renderer?.domElement.remove();
    fail();
    console.error('Unable to display OBJ trophy:', error);
  }
}

const pending = document.querySelectorAll('.trophy-obj');
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      showTrophy(entry.target);
    }
  }, { rootMargin: '200px' });
  pending.forEach((element) => observer.observe(element));
} else {
  pending.forEach(showTrophy);
}
