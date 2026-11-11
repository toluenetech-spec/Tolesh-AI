/* The voice orb, drawn as a sphere of particles.

   The design comes from the supplied mockup: a dense field of small cyan dots
   over a dark ground, with a bright irregular rim where the surface turns away
   from the viewer. It is drawn here rather than shipped as a still image for
   one reason: a still image cannot react, and an orb that pulses on a timer
   while claiming to show a microphone level is a lie the previous version of
   this file told. Every number that moves this sphere comes from somewhere
   real — meter.js writes the microphone's RMS in, and the four voice states
   come from the listener.

     idle       slow rotation, low brightness, no displacement
     listening  radius and jitter follow the measured level
     speaking   the level is held at zero (it is Tolesh's voice, not yours) and
                a slow swell marks the state instead
     paused     rotation stops and the field dims; nothing is being heard

   Cost: about 1,100 particles, one fillRect each, two passes. That is well
   inside a 60 Hz frame on a laptop and is deliberately cheaper than a WebGL
   context, which is a lot of machinery for a 68 px circle.

   Reduced motion: no rotation, no jitter, and redraws only when something
   actually changes. The sphere is still there; it just stops spinning.
*/

const TAU = Math.PI * 2;

/* Deterministic noise, so the sphere looks the same on every load instead of
   reshuffling itself each time the page is opened. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Brightness clusters on the surface — the mockup's uneven glow. */
function cluster(x, y, z) {
  const a = Math.sin(x * 3.1 + 1.7) * Math.cos(y * 2.4 - 0.4);
  const b = Math.sin(z * 2.2 + 2.1) * Math.cos(x * 1.6 - 1.1);
  return Math.min(1, Math.max(0, 0.5 + 0.42 * a + 0.34 * b));
}

export class ParticleOrb {
  constructor(canvas, { count = 600, tint = [96, 224, 255], seed = 0x7013 } = {}) {
    this.canvas = canvas;
    this.ctx = canvas ? canvas.getContext('2d', { alpha: true }) : null;
    this.count = count;
    this.tint = tint;
    this.level = 0;
    this.state = 'idle';
    this.rotation = 0;
    this.tilt = -0.22;
    this._seed = seed;
    this._raf = 0;
    this._running = false;
    this._last = 0;
    this._time = 0;
    this._dpr = 1;
    this._w = 0;
    this._h = 0;
    this._reduced = !!(window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    this._dirty = true;
    this._particles = [];
    this._onResize = () => this.resize();
    this._onVisibility = () => {
      if (document.hidden) this._pauseLoop();
      else if (this._running) this._startLoop();
    };

    if (!this.ctx) return;
    this._buildSprite();
    this._build();
    this.resize();
    window.addEventListener('resize', this._onResize, { passive: true });
    document.addEventListener('visibilitychange', this._onVisibility);
    // Draw one frame immediately: an empty canvas would flash before the first
    // animation frame arrives.
    this._draw(0);
  }

  /* ------------------------------------------------------------- geometry */
  _buildSprite() {
    // One soft round dot, drawn once and stamped per particle. ``fillRect``
    // would be faster still, but a square dot is a square dot: at 68 px it is
    // invisible, and the moment the same field is drawn large it looks like
    // pixels rather than light.
    const size = 16;
    const sprite = document.createElement('canvas');
    sprite.width = size;
    sprite.height = size;
    const g = sprite.getContext('2d');
    const [r, gr, b] = this.tint;
    const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    // A tight core with a short falloff: a wide gradient turns a field of
    // dots into a fog.
    // The core is the tint, not white. A white core averages into grey when
    // the sprite is scaled down to two pixels, which is exactly the size the
    // rail orb uses — the sphere came out looking like a dusty ball.
    grad.addColorStop(0, `rgba(${r},${gr},${b},1)`);
    grad.addColorStop(0.22, `rgba(${r},${gr},${b},0.92)`);
    grad.addColorStop(0.55, `rgba(${r},${gr},${b},0.34)`);
    grad.addColorStop(1, `rgba(${r},${gr},${b},0)`);
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    this._sprite = sprite;
  }

  _build() {
    const random = mulberry32(this._seed);
    const particles = [];
    // Rings of latitude, each sized by its circumference, so the dots are
    // evenly spaced on the surface rather than bunched at the poles. This is
    // also what produces the mockup's rows of dots.
    const rings = 26;
    let placed = 0;
    for (let i = 0; i < rings; i += 1) {
      const v = (i + 0.5) / rings;
      const lat = Math.asin(2 * v - 1);          // -π/2 … π/2
      const ringRadius = Math.cos(lat);
      const perRing = Math.max(4, Math.round(this.count * ringRadius / rings * 1.55));
      for (let j = 0; j < perRing; j += 1) {
        const lon = (j / perRing) * TAU + i * 0.31;
        const x = Math.cos(lat) * Math.cos(lon);
        const y = Math.sin(lat);
        const z = Math.cos(lat) * Math.sin(lon);
        // A little radial noise keeps the silhouette from looking machined.
        const shell = 1 + (random() - 0.5) * 0.075;
        particles.push({
          x: x * shell,
          y: y * shell,
          z: z * shell,
          size: 0.9 + random() * 1.0,
          glow: cluster(x, y, z),
          phase: random() * TAU,
        });
        placed += 1;
      }
    }
    this._particles = particles;
    this._placed = placed;
  }

  resize() {
    if (!this.canvas || !this.ctx) return;
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width || this.canvas.clientWidth || 68));
    const height = Math.max(1, Math.round(rect.height || this.canvas.clientHeight || 68));
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    if (width === this._w && height === this._h && dpr === this._dpr) return;
    this._w = width;
    this._h = height;
    this._dpr = dpr;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    this._dirty = true;
    if (!this._running || this._reduced) this._draw(0);
  }

  /* ---------------------------------------------------------------- input */
  setLevel(level) {
    const next = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0;
    if (Math.abs(next - this.level) < 0.004) return;
    this.level = next;
    this._dirty = true;
    if (this._reduced) this._draw(0);
  }

  setState(state) {
    if (state === this.state) return;
    this.state = state;
    if (state !== 'listening') this.level = 0;
    this._dirty = true;
    if (this._reduced) this._draw(0);
  }

  /* --------------------------------------------------------------- drawing */
  _brightness() {
    switch (this.state) {
      case 'listening': return 1.3;
      case 'speaking': return 1.2;
      case 'paused': return 0.5;
      default: return 1.05;
    }
  }

  _draw(dt) {
    const ctx = this.ctx;
    if (!ctx) return;
    const w = this._w;
    const h = this._h;
    const dpr = this._dpr;
    const cx = w / 2;
    const cy = h / 2;
    const base = Math.min(w, h) * 0.415;
    this._scale = Math.pow(w / 68, 0.6);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // Additive: overlapping particles add light, the way the mockup's dots do.
    // Composited normally they average toward grey and the cyan disappears.
    ctx.globalCompositeOperation = 'lighter';

    // Speaking swells on a slow sine: a state indicator, not a measurement.
    const swell = this.state === 'speaking'
      ? 0.5 + 0.5 * Math.sin(this._time * 2.4)
      : 0;
    const breathe = 1 + this.level * 0.085 + swell * 0.018;
    const radius = base * breathe;
    const [r, g, b] = this.tint;
    const bright = this._brightness() * (0.92 + this.level * 0.5 + swell * 0.12);

    // The halo behind the sphere, brightest just inside the rim.
    const halo = ctx.createRadialGradient(cx, cy, radius * 0.1, cx, cy, radius * 1.22);
    halo.addColorStop(0, `rgba(${r},${g},${b},${0.02 + this.level * 0.05})`);
    halo.addColorStop(0.62, `rgba(${r},${g},${b},${0.05 + this.level * 0.06})`);
    halo.addColorStop(0.88, `rgba(${r},${g},${b},${0.13 + this.level * 0.10 + swell * 0.03})`);
    halo.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.22, 0, TAU);
    ctx.fill();

    const cosY = Math.cos(this.rotation);
    const sinY = Math.sin(this.rotation);
    const cosX = Math.cos(this.tilt);
    const sinX = Math.sin(this.tilt);
    const jitter = this.level * 0.05 + swell * 0.006;

    // Back half first, so the front dots sit on top of them. Splitting by sign
    // of z is cheaper than a sort and looks the same at this size.
    for (let pass = 0; pass < 2; pass += 1) {
      const wantFront = pass === 1;
      for (let i = 0; i < this._particles.length; i += 1) {
        const p = this._particles[i];
        // Rotate around Y, then tilt around X.
        const x1 = p.x * cosY + p.z * sinY;
        const z1 = -p.x * sinY + p.z * cosY;
        const y2 = p.y * cosX - z1 * sinX;
        const z2 = p.y * sinX + z1 * cosX;
        if ((z2 >= 0) !== wantFront) continue;

        const depth = (z2 + 1) / 2;                    // 0 back … 1 front
        // Near the silhouette the surface turns away, which is where the
        // mockup's bright rim comes from.
        const rim = Math.pow(1 - Math.abs(z2), 7);
        const push = 1 + jitter * Math.sin(p.phase + this._time * 5.5) * (1 - Math.abs(z2));
        const px = cx + x1 * radius * push;
        const py = cy + y2 * radius * push;
        // Sub-linear in canvas size: doubling the orb should show more of the
        // sphere's detail, not double the diameter of every dot.
        const size = (p.size * (0.5 + depth * 0.85) + rim * 1.5 + this.level * 0.6)
          * this._scale;
        // Kept low on purpose: additive light saturates to white long before
        // it reaches alpha 1, and a white sphere is not the mockup.
        const alpha = Math.min(0.9, (0.07 + depth * 0.3 + rim * 0.52
          + p.glow * 0.16) * bright);
        if (alpha <= 0.01) continue;
        const d = size * 1.9;          // the sprite has a soft falloff
        ctx.globalAlpha = alpha;
        ctx.drawImage(this._sprite, px - d / 2, py - d / 2, d, d);
      }
    }

    ctx.globalAlpha = 1;

    // A small hot core, matching the brighter centre of the mockup.
    const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 0.34);
    core.addColorStop(0, `rgba(255,255,255,${0.10 + this.level * 0.16 + swell * 0.05})`);
    core.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.34, 0, TAU);
    ctx.fill();
    ctx.globalCompositeOperation = 'source-over';
  }

  /* ------------------------------------------------------------- lifecycle */
  _startLoop() {
    if (this._raf) return;
    this._last = performance.now();
    const step = (now) => {
      this._raf = 0;
      if (!this._running) return;
      const dt = Math.min(0.05, (now - this._last) / 1000);
      this._last = now;
      this._time += dt;
      // Idle and listening turn; paused and reduced-motion do not.
      if (this.state !== 'paused') this.rotation += dt * (0.16 + this.level * 0.5);
      this._draw(dt);
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  _pauseLoop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = 0;
  }

  start() {
    if (!this.ctx || this._running) return;
    this._running = true;
    if (this._reduced) {
      // One still frame, redrawn only when the level or state changes.
      this._draw(0);
      return;
    }
    if (!document.hidden) this._startLoop();
  }

  stop() {
    this._running = false;
    this._pauseLoop();
  }

  destroy() {
    this.stop();
    window.removeEventListener('resize', this._onResize);
    document.removeEventListener('visibilitychange', this._onVisibility);
  }
}
