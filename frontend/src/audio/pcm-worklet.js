// AudioWorklet: микрофон → PCM s16le 16 кГц моно, кадры по 20 мс (320 отсчётов).
// Отдельный файл, а не Blob URL: CSP script-src 'self' не пускает worklet из blob:.
// Константы совпадают с TARGET_RATE / FRAME_SAMPLES в pcmWorklet.ts.
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0; this.n = 0; this.pos = 0;
    this.out = new Int16Array(320); this.k = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i]; this.n++; this.pos++;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        let v = this.acc / this.n; this.acc = 0; this.n = 0;
        v = v < -1 ? -1 : v > 1 ? 1 : v;
        this.out[this.k++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.k === 320) {
          this.port.postMessage(this.out.buffer, [this.out.buffer]);
          this.out = new Int16Array(320); this.k = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-downsampler", PcmDownsampler);
