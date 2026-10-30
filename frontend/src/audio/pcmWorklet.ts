// AudioWorklet: микрофон (частота контекста, обычно 44.1/48 кГц) → PCM s16le 16 кГц моно, кадры по 20 мс.
// Передискретизация — усреднение входных отсчётов внутри каждого выходного (простой ФНЧ),
// для распознавания речи этого достаточно. Сам процессор — в pcm-worklet.js.

export const TARGET_RATE = 16000;
export const FRAME_SAMPLES = 320; // 20 мс при 16 кГц

/** URL worklet-модуля (src/audio/pcm-worklet.js): Vite кладёт его в assets, CSP — 'self'. */
export { default as WORKLET_URL } from "./pcm-worklet.js?url&no-inline";

/** Заголовок WAV для потоковой записи: размеры «до конца файла» (0xFFFFFFFF), как у потоковых WAV. */
export function streamingWavHeader(rate = TARGET_RATE): ArrayBuffer {
  const b = new ArrayBuffer(44);
  const v = new DataView(b);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 0xffffffff, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // моно
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, 0xffffffff - 36, true);
  return b;
}
