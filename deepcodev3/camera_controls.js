// camera_controls.js
// Camera + OrbitControls setup for the destructible-building demo.
// Exposes window.UC_Camera = { setupCamera, setupOrbitControls }.

(function () {
  'use strict';

  const INITIAL_POS = { x: 18, y: 14, z: 28 };
  const INITIAL_TARGET = { x: 0, y: 4, z: 0 };
  const FRAME_DISTANCE = 28;

  function setupCamera(aspect) {
    const camera = new THREE.PerspectiveCamera(55, aspect, 0.1, 400);
    // logarithmicDepthBuffer is a renderer-level flag; near/far above are chosen
    // to stay well-conditioned whether or not the renderer enables it.
    camera.position.set(INITIAL_POS.x, INITIAL_POS.y, INITIAL_POS.z);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    return camera;
  }

  function setupOrbitControls(camera, domElement) {
    if (typeof THREE.OrbitControls !== 'function') {
      throw new Error('THREE.OrbitControls is not loaded');
    }
    const controls = new THREE.OrbitControls(camera, domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.minDistance = 4;
    controls.maxDistance = 120;
    controls.maxPolarAngle = Math.PI * 0.495;
    controls.target.set(INITIAL_TARGET.x, INITIAL_TARGET.y, INITIAL_TARGET.z);
    controls.update();
    return controls;
  }

  function frameBuilding(camera, controls) {
    controls.target.set(INITIAL_TARGET.x, INITIAL_TARGET.y, INITIAL_TARGET.z);
    // Place camera FRAME_DISTANCE away from target along current view direction.
    const dir = new THREE.Vector3()
      .subVectors(camera.position, controls.target)
      .normalize();
    if (dir.lengthSq() < 1e-6) {
      dir.set(0.6, 0.35, 0.72).normalize();
    }
    camera.position.copy(controls.target).addScaledVector(dir, FRAME_DISTANCE);
    controls.update();
  }

  function resetCamera(camera, controls) {
    camera.position.set(INITIAL_POS.x, INITIAL_POS.y, INITIAL_POS.z);
    controls.target.set(INITIAL_TARGET.x, INITIAL_TARGET.y, INITIAL_TARGET.z);
    controls.update();
  }

  function bindHotkeys(camera, controls) {
    function onKeyDown(e) {
      if (e.repeat) return;
      const tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target && e.target.isContentEditable) return;
      const k = e.key.toLowerCase();
      if (k === 'f') {
        frameBuilding(camera, controls);
        e.preventDefault();
      } else if (k === 'r') {
        resetCamera(camera, controls);
        e.preventDefault();
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return function unbind() {
      window.removeEventListener('keydown', onKeyDown);
    };
  }

  function setup(aspect, domElement) {
    const camera = setupCamera(aspect);
    const controls = setupOrbitControls(camera, domElement);
    const unbind = bindHotkeys(camera, controls);
    return { camera, controls, bindHotkeys: unbind };
  }

  window.UC_Camera = {
    setupCamera: setupCamera,
    setupOrbitControls: setupOrbitControls,
    bindHotkeys: bindHotkeys,
    setup: setup
  };
})();
