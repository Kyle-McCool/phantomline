/* Landing page progressive enhancement.
   The 2026-05 redesign replaced the Three.js hero scene (canvas#heroThree
   + animated phone-mock) with editorial typography. The Three.js code path
   is preserved below, gated on the canvas existing — so if a future
   redesign brings the hero scene back, this file lights up automatically.

   Currently this script does nothing visible. We keep the file linked
   from landing.html so the Cache-Control + versioned asset pipeline
   stays consistent across pages.
*/

const canvas = document.getElementById("heroThree");

if (canvas) {
  // Hero Three.js scene (only runs if the canvas is present).
  import("https://unpkg.com/three@0.160.0/build/three.module.js").then((THREE) => {
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 100);
    camera.position.set(0, 0.2, 7);

    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    const group = new THREE.Group();
    scene.add(group);

    const cyan = new THREE.Color("#1ab8e8");

    const pointLight = new THREE.PointLight(cyan, 2.2, 16);
    pointLight.position.set(2.8, 2.4, 3.6);
    scene.add(pointLight);
    scene.add(new THREE.AmbientLight("#ffffff", 0.42));

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
      group.rotation.y = Math.sin(t * 0.22) * 0.12;
      renderer.render(scene, camera);
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  });
}

/* ─── Hamburger menu toggle ─────────────────────────────────────────── */
const hamburger = document.querySelector('.nav-hamburger');
const overlay = document.querySelector('.nav-mobile-overlay');

const iconMenu = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg>';
const iconClose = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>';

function closeMenu() {
  if (!overlay) return;
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  if (hamburger) {
    hamburger.setAttribute('aria-expanded', 'false');
    hamburger.innerHTML = iconMenu;
  }
  document.body.style.overflow = '';
}

if (hamburger && overlay) {
  hamburger.addEventListener('click', () => {
    overlay.classList.add('open');
    hamburger.setAttribute('aria-expanded', 'true');
    hamburger.innerHTML = iconClose;
    overlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  });

  const closeBtn = overlay.querySelector('.nav-mobile-close');
  if (closeBtn) closeBtn.addEventListener('click', closeMenu);

  overlay.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', closeMenu);
  });
}
