/* Microphone level meter.

   A real measurement, not an animation: getUserMedia → MediaStreamSource →
   AnalyserNode, and the level is the RMS of the actual time-domain samples.
   Nothing here draws a waveform when there is no audio — if the microphone
   cannot be opened, the orb stays still and says why.

   Four states, because they mean different things:

     idle       no stream, nothing is being captured
     listening  the stream is open and the level is live
     speaking   Tolesh is talking; the microphone is still open (so barge-in
                can stop the speech) but its level is not shown, because
                showing the assistant's own voice as "you" would be a lie
     paused     the session is alive but the level is held at zero

   Every path out — stop(), a rejected permission, an unplugged device, the
   page going away — ends the tracks and closes the AudioContext. A microphone
   left open is a privacy problem and a battery problem.
*/

export const MIC_STATES = ['idle', 'listening', 'speaking', 'paused'];

/** RMS → 0..1, with a curve that makes quiet speech visible without clipping
 *  loud speech. Chosen from measured speech RMS (~0.02–0.2) rather than taste. */
export function levelFromRms(rms) {
  if (!Number.isFinite(rms) || rms <= 0) return 0;
  return Math.min(1, Math.pow(Math.min(1, rms * 5), 0.6));
}

export class MicMeter {
  constructor({ onLevel, onState, onError, target, fftSize = 1024 } = {}) {
    this.onLevel = onLevel || (() => {});
    this.onState = onState || (() => {});
    this.onError = onError || (() => {});
    // Where the CSS variable is written. Defaults to the orb if it exists.
    this.target = target || null;
    this.fftSize = fftSize;

    this.stream = null;
    this.context = null;
    this.analyser = null;
    this._buffer = null;
    this._frame = 0;
    this._level = 0;
    this._state = 'idle';
    this._running = false;
  }

  get state() { return this._state; }
  get level() { return this._level; }
  get active() { return this.stream !== null; }

  get supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia
              && (window.AudioContext || window.webkitAudioContext));
  }

  _setState(state) {
    if (!MIC_STATES.includes(state) || state === this._state) return;
    this._state = state;
    const el = this._element();
    if (el) el.dataset.voiceState = state;
    this.onState(state);
  }

  _element() {
    if (this.target) return this.target;
    return document.getElementById('voiceOrb') || document.body;
  }

  _paint(level) {
    this._level = level;
    const el = this._element();
    if (el) el.style.setProperty('--mic-level', level.toFixed(3));
    this.onLevel(level);
  }

  async start() {
    if (this.active) {
      if (this._state !== 'listening') this._setState('listening');
      return true;
    }
    if (!this.supported) {
      this.onError('This browser cannot open a microphone (no Web Audio or no '
                   + 'getUserMedia). The level display stays off.');
      return false;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (err) {
      // NotAllowedError is a decision, not a fault — say which.
      const denied = err && (err.name === 'NotAllowedError'
                             || err.name === 'SecurityError');
      this.onError(denied
        ? 'Microphone permission was denied, so the level cannot be shown.'
        : `The microphone could not be opened: ${(err && err.message) || err}`);
      this._setState('idle');
      return false;
    }

    try {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      const context = new Ctor();
      // A context created outside a gesture starts suspended; resuming is what
      // makes the samples flow.
      if (context.state === 'suspended') await context.resume();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = this.fftSize;
      analyser.smoothingTimeConstant = 0.75;
      source.connect(analyser);
      // Deliberately not connected to the destination: routing the microphone
      // to the speakers would feed back.
      this.context = context;
      this.analyser = analyser;
      this._buffer = new Float32Array(analyser.fftSize);
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      this.onError(`The audio graph could not be built: ${(err && err.message) || err}`);
      this._setState('idle');
      return false;
    }

    this.stream = stream;
    // A device that goes away (unplugged, permission revoked) must not leave
    // the orb pretending to listen.
    for (const track of stream.getAudioTracks()) {
      track.addEventListener('ended', () => {
        this.onError('The microphone stopped. The level display is off.');
        this.stop();
      });
    }
    this._setState('listening');
    this._running = true;
    this._loop();
    return true;
  }

  setState(state) {
    if (state === 'idle') { this.stop(); return; }
    if (!this.active) return;          // nothing to relabel
    this._setState(state);
    if (state !== 'listening') {
      // Speaking or paused: hold the display at zero instead of showing the
      // assistant's own voice as input.
      this._paint(0);
    }
  }

  _loop() {
    if (!this._running) return;
    this._frame = requestAnimationFrame(() => this._tick());
  }

  _tick() {
    if (!this._running || !this.analyser) return;
    if (this._state === 'listening') {
      this.analyser.getFloatTimeDomainData(this._buffer);
      let sum = 0;
      for (let i = 0; i < this._buffer.length; i += 1) {
        const v = this._buffer[i];
        sum += v * v;
      }
      const target = levelFromRms(Math.sqrt(sum / this._buffer.length));
      // Fast attack, slow release: the orb follows a syllable up and settles
      // back down instead of strobing on every frame.
      const next = target > this._level
        ? this._level * 0.35 + target * 0.65
        : this._level * 0.82 + target * 0.18;
      this._paint(next < 0.005 ? 0 : next);
    }
    this._loop();
  }

  stop() {
    this._running = false;
    if (this._frame) {
      cancelAnimationFrame(this._frame);
      this._frame = 0;
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) {
        try { track.stop(); } catch (_) { /* already ended */ }
      }
      this.stream = null;
    }
    if (this.context) {
      try { this.context.close(); } catch (_) { /* already closed */ }
      this.context = null;
    }
    this.analyser = null;
    this._buffer = null;
    this._paint(0);
    this._setState('idle');
  }
}
