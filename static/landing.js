  import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js";

  const canvas = document.getElementById("heroThree");
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 100);
  camera.position.set(0, 0.2, 7);

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const group = new THREE.Group();
  scene.add(group);

  const mint = new THREE.Color("#6ee7b7");
  const cyan = new THREE.Color("#26e7f5");
  const signal = new THREE.Color("#8ab4ff");

  const pointLight = new THREE.PointLight(cyan, 2.2, 16);
  pointLight.position.set(2.8, 2.4, 3.6);
  scene.add(pointLight);
  scene.add(new THREE.AmbientLight("#8ab4ff", 0.42));

  const phoneShape = new THREE.Shape();
  const w = 1.28, h = 2.45, r = 0.20;
  phoneShape.moveTo(-w + r, -h);
  phoneShape.lineTo(w - r, -h);
  phoneShape.quadraticCurveTo(w, -h, w, -h + r);
  phoneShape.lineTo(w, h - r);
  phoneShape.quadraticCurveTo(w, h, w - r, h);
  phoneShape.lineTo(-w + r, h);
  phoneShape.quadraticCurveTo(-w, h, -w, h - r);
  phoneShape.lineTo(-w, -h + r);
  phoneShape.quadraticCurveTo(-w, -h, -w + r, -h);
  const phoneGeo = new THREE.ExtrudeGeometry(phoneShape, { depth: 0.08, bevelEnabled: true, bevelThickness: 0.035, bevelSize: 0.035, bevelSegments: 8 });
  const phoneMat = new THREE.MeshStandardMaterial({ color: "#0b1112", roughness: 0.32, metalness: 0.44, emissive: "#071516", emissiveIntensity: 0.4 });
  const phone = new THREE.Mesh(phoneGeo, phoneMat);
  phone.position.set(2.25, -0.15, -0.25);
  phone.rotation.set(-0.08, -0.36, 0.08);
  group.add(phone);

  const screenGeo = new THREE.PlaneGeometry(2.18, 4.28, 1, 1);
  const screenMat = new THREE.MeshBasicMaterial({ color: "#081112", transparent: true, opacity: 0.74 });
  const screen = new THREE.Mesh(screenGeo, screenMat);
  screen.position.set(2.25, -0.15, -0.15);
  screen.rotation.copy(phone.rotation);
  group.add(screen);

  const particles = new THREE.BufferGeometry();
  const count = 190;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    positions[i * 3] = (Math.random() - 0.5) * 11;
    positions[i * 3 + 1] = (Math.random() - 0.5) * 6;
    positions[i * 3 + 2] = (Math.random() - 0.5) * 4 - 1;
    const c = i % 3 === 0 ? cyan : i % 3 === 1 ? mint : signal;
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
  }
  particles.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  particles.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const particleMat = new THREE.PointsMaterial({ size: 0.028, vertexColors: true, transparent: true, opacity: 0.78 });
  const field = new THREE.Points(particles, particleMat);
  scene.add(field);

  let mouseX = 0, mouseY = 0;
  window.addEventListener("pointermove", (event) => {
    mouseX = (event.clientX / window.innerWidth - 0.5) * 0.5;
    mouseY = (event.clientY / window.innerHeight - 0.5) * 0.35;
  });

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / Math.max(1, rect.height);
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  function tick(time) {
    const t = time * 0.001;
    group.rotation.y = Math.sin(t * 0.22) * 0.12 + mouseX;
    group.rotation.x = Math.sin(t * 0.18) * 0.045 - mouseY;
    group.position.y = Math.sin(t * 0.7) * 0.08;
    field.rotation.y = t * 0.035;
    field.rotation.x = Math.sin(t * 0.12) * 0.08;
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
