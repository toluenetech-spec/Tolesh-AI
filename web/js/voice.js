/* Browser-native voice interaction for Tolesh.

   Uses the Web Speech API (SpeechRecognition + SpeechSynthesis) which is
   available in Chromium/Electron/QtWebEngine-based shells. This is REAL
   speech-to-text and text-to-speech, not a simulated waveform.

   Honest limitation: wake-word, neural VAD and native Piper/Chatterbox TTS
   belong to the Python foundation (src/tolesh/listening, src/tolesh/output);
   this module is the browser-side voice surface. Where the API is unavailable,
   it reports that plainly instead of pretending to work.

   Voice interruption (barge-in): any recognised speech cancels current TTS.
*/

export class VoiceController {
  constructor({ onResult, onInterim, onState, onError } = {}) {
    this.onResult = onResult || (() => {});
    this.onInterim = onInterim || (() => {});
    this.onState = onState || (() => {});
    this.onError = onError || (() => {});
    this.recognition = null;
    this.active = false;
    this.wantContinuous = false;
    this.finalBuffer = [];
    this.speakOnReply = true;
  }

  get supported() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  get ttsSupported() {
    return 'speechSynthesis' in window;
  }

  start() {
    if (this.active) return;
    if (!this.supported) {
      this.onError('Speech recognition is not available in this browser. Type instead, or use the desktop build with the native voice pipeline.');
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
    this.onState('idle');
  }

  toggle() {
    if (this.active) this.stop(); else this.start();
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
    u.onstart = () => { if (typeof onStart === 'function') onStart(); };
    u.onend = () => { if (typeof onEnd === 'function') onEnd(); };
    u.onerror = () => { if (typeof onEnd === 'function') onEnd(); };
    window.speechSynthesis.speak(u);
    return true;
  }

  stopSpeaking() {
    if (this.ttsSupported) window.speechSynthesis.cancel();
  }
}
