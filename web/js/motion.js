/* Motion orchestration for Tolesh.

   GSAP is vendored locally (web/vendor/gsap.min.js). We use:
     - gsap.timeline() for one orchestrated, staggered page entrance
       (frontend-design: one well-orchestrated load beats scattered effects)
     - gsap.matchMedia() to scope motion to no-preference users
       (gsap-core: responsive + prefers-reduced-motion)
   A Web Animations API fallback keeps the UI alive if GSAP fails to load.
   All CSS animations also honour prefers-reduced-motion.
*/

export function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function initMotion() {
  if (prefersReducedMotion()) return;

  if (window.gsap && typeof window.gsap.matchMedia === 'function') {
    const gsap = window.gsap;
    const mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
      tl.from('.rail', { x: -18, opacity: 0, duration: 0.5 })
        .from('.topbar > *', { y: -10, opacity: 0, stagger: 0.07, duration: 0.42 }, '-=0.26')
        .from('.empty-state > *', { y: 16, opacity: 0, stagger: 0.08, duration: 0.52 }, '-=0.2')
        .from('.context .panel-section', { x: 14, opacity: 0, stagger: 0.055, duration: 0.42 }, '-=0.34')
        .from('.composer', { y: 14, opacity: 0, duration: 0.46 }, '-=0.3');

      // A single settling halo, not an endless loop. Web Interface Guidelines
      // require pause/stop controls for autoplay motion longer than 5s, and
      // frontend-design favours one orchestrated moment over scattered loops.
      // The listening pulse below is user-triggered and stops with the session.
      gsap.fromTo('.orb-ring-1',
        { scale: 0.88, opacity: 0 },
        { scale: 1.08, opacity: 0.3, duration: 1.6, ease: 'sine.out' });
    });
  } else {
    const targets = document.querySelectorAll(
      '.rail, .topbar > *, .empty-state > *, .context .panel-section, .composer');
    targets.forEach((el, i) => {
      el.animate(
        [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }],
        { duration: 420, delay: i * 42, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'both' }
      );
    });
  }
}

/** Gentle reveal for content added after load (e.g. a new context card). */
export function animateIn(el) {
  if (!el || prefersReducedMotion()) return;
  if (window.gsap) {
    window.gsap.from(el, { y: 10, opacity: 0, duration: 0.32, ease: 'power2.out' });
  } else {
    el.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
      { duration: 300, easing: 'cubic-bezier(.22,1,.36,1)' });
  }
}

export function setOrbListening(active) {
  const orb = document.getElementById('voiceOrb');
  const caption = document.getElementById('orbCaption');
  const btn = document.getElementById('voiceBtn');
  if (!orb) return;
  orb.classList.toggle('is-listening', active);
  btn?.classList.toggle('is-active', active);
  if (caption) caption.textContent = active ? 'Listening' : 'Voice';
  orb.setAttribute('aria-label', active ? 'Stop voice conversation' : 'Start a voice conversation');
  if (active && window.gsap && !prefersReducedMotion()) {
    window.gsap.fromTo('.orb-ring-2', { scale: 0.85, opacity: 0.6 },
      { scale: 1.25, opacity: 0, duration: 1.4, repeat: -1, ease: 'power1.out' });
  } else if (window.gsap) {
    window.gsap.killTweensOf('.orb-ring-2');
    window.gsap.set('.orb-ring-2', { opacity: 0 });
  }
}
