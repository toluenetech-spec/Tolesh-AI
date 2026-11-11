/* Browser-native voice interaction for Tolesh.

   Uses the Web Speech API (SpeechRecognition + SpeechSynthesis) which is
   available in Chromium/Electron/QtWebEngine-based shells. This is REAL
   speech-to-text and text-to-speech, not a simulated waveform.

   Honest limitation: wake-word, neural VAD and native Piper/Chatterbox TTS
   belong to the Python foundation (src/tolesh/listening, src/tolesh/output);
   this module is the browser-side voice surface. Where the API is unavailable,
   it reports that plainly instead of pretending to work.

   Voice interruption (barge-in): any recognised speech cancels current TTS.

   The level display is a separate concern and lives in meter.js: it reads the
   real microphone through an AnalyserNode, so it keeps working (and keeps
   telling the truth) when the Web Speech API is unavailable.
*/

import { MicMeter } from './meter.js';

export class VoiceController {
  constructor({ onResult, onInterim, onState, onError, onLevel, onMicState,
                meter } = {}) {
    this.onResult = onResult || (() => {});
    this.onInterim = onInterim || (() => {});
    this.onState = onState || (() => {});
    this.onError = onError || (() => {});
    this.onLevel = onLevel || (() => {});
    // The meter has its own four states; the orb draws them, and they are not
    // the same as the recognition states (recognition can be unavailable while
    // the microphone is open and measuring).
    this.onMicState = onMicState || (() => {});
    this.recognition = null;
    this.active = false;
    this.wantContinuous = false;
    this.finalBuffer = [];
    this.speakOnReply = true;
    // The level display is a separate module because it is a separate fact:
    // it reads the microphone directly, so it stays truthful when the Web
    // Speech API is present but its service is not reachable.
    this.meter = meter || new MicMeter({
      onError: (msg) => this.onError(msg),
      onLevel: (level) => this.onLevel(level),
      onState: (s) => this.onMicState(s),
    });
  }

  get supported() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }



  get ttsSupported() {
    return 'speechSynthesis' in window;
  }

  start() {
    if (this.active) return;
    // The level display does not depend on the speech service, so the
    // microphone opens first. Clicking the orb in a browser without
    // SpeechRecognition used to return before the meter started, which left
    // the orb inert and contradicted the promise below; now the user gets a
    // live level and a sentence explaining the missing engine.
    void this.meter.start();
    if (!this.supported) {
      this.onError('Speech recognition is not available in this browser. The microphone level still works. Type instead, or use the desktop build with the native voice pipeline.');
      return;
    }
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new Ctor();
    rec.lang = 'en-NG';            // Nigerian English; also understands Pidgin reasonably
    rec.continuous = true;         // continuous conversation mode
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    this.recognition = rec;
    this.finalBuffer = [];
    this.active = true;
    this.wantContinuous = true;
    this.onState('listening');

    rec.onresult = (event) => {
      // Barge-in: the moment the user speaks, stop any Tolesh speech.
      this.stopSpeaking();
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) this.finalBuffer.push(transcript.trim());
        else interim += transcript;
      }
      if (interim) this.onInterim(interim);
      if (this.finalBuffer.length) {
        const text = this.finalBuffer.join(' ').trim();
        this.finalBuffer = [];
        if (text) this.onResult(text);
      }
    };
    rec.onerror = (event) => {
      // 'no-speech' / 'aborted' are normal in continuous mode.
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        this.onError('Microphone permission was denied. Allow it in your browser settings.');
        this.stop();
      } else if (event.error === 'network') {
        this.onError('Speech service unreachable. Check your connection or type instead.');
      }
    };
    rec.onend = () => {
      if (this.wantContinuous && this.active) {
        try { rec.start(); } catch (_) { /* already starting */ }
      } else {
        this.active = false;
        this.meter.stop();
        this.onState('idle');
      }
    };
    try { rec.start(); } catch (err) {
      this.active = false;
      this.onError(`Could not start voice input: ${err.message}`);
    }
  }

  stop() {
    this.wantContinuous = false;
    this.active = false;
    if (this.recognition) {
      try { this.recognition.stop(); } catch (_) { /* noop */ }
      this.recognition.onend = null;
      this.recognition = null;
    }
    // Nothing should be holding the microphone once the session is over.
    this.meter.stop();
    this.onState('idle');
  }

  toggle() {
    // A browser with no recognition engine can still be listening to the
    // microphone, so "active" is not the only thing that keeps the orb alive.
    if (this.active || this.meter.active) this.stop(); else this.start();
  }

  speak(text, { onStart, onEnd } = {}) {
    if (!this.speakOnReply || !this.ttsSupported || !text) return false;
    const plain = String(text).replace(/[`*_#]/g, '').slice(0, 1200);
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(plain);
    u.lang = 'en-NG';
    u.rate = 0.98;
    u.pitch = 0.92;
    u.volume = 1;
    // Reported from the synthesiser itself, so a caller that shows "Speaking"
    // is showing audio that exists, not audio that was requested.
    u.onstart = () => {
      // While Tolesh talks, the microphone is still open (that is what makes
      // barge-in possible) but its level is not shown: it would be Tolesh's
      // own voice.
      this.meter.setState('speaking');
      if (typeof onStart === 'function') onStart();
    };
    const settled = () => {
      this.meter.setState(this.active ? 'listening' : 'idle');
      if (typeof onEnd === 'function') onEnd();
    };
    u.onend = settled;
    u.onerror = settled;
    window.speechSynthesis.speak(u);
    return true;
  }

  stopSpeaking() {
    if (this.ttsSupported) window.speechSynthesis.cancel();
    // cancel() does not reliably fire onend, so restore the meter here.
    this.meter.setState(this.active ? 'listening' : 'idle');
  }
}
