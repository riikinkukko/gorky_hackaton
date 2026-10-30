// Микрофон «наготове» с кольцевым буфером (pre-roll) и дозаписью после отпускания (post-roll).
//
// Почему не MediaRecorder: он стартует 100–500 мс после нажатия, и первый слог теряется
// («Удали» → «Дали»). Здесь поток с микрофона идёт постоянно, пока страница активна,
// последние PREROLL_MS лежат в кольцевом буфере и уходят на сервер вместе с записью.
// На сервер идёт потоковый WAV (PCM 16 кГц моно): {"type":"start","mime":"audio/wav"} → заголовок →
// pre-roll → кадры по ходу записи → ещё POSTROLL_MS → {"type":"end"}.
//
// iOS Safari держать поток постоянно не стоит (оранжевый индикатор, поток рвётся в фоне), поэтому там
// микрофон открывается по pointerdown, «Говорите…» появляется только когда пошли реальные кадры,
// а после записи поток живёт ещё 15 с — на уточняющий ответ в диалоге.

import { FRAME_SAMPLES, streamingWavHeader, TARGET_RATE, WORKLET_URL } from "./pcmWorklet";

export const PREROLL_MS = 400;
export const POSTROLL_MS = 400;
/** Склеиваем кадры по 20 мс в куски по ~100 мс, чтобы не дробить WebSocket. */
const SEND_EVERY_SAMPLES = (TARGET_RATE * 100) / 1000;
/** Микрофон наготове гасим, если им не пользуются: индикатор записи не должен гореть часами. */
const IDLE_RELEASE_MS = 5 * 60_000;
/** iOS: после записи держим поток 15 с — уточняющий ответ в диалоге тоже получит pre-roll. */
const IOS_LINGER_MS = 15_000;

export const MIME = "audio/wav";

export type EngineError = "denied" | "error" | "unsupported";

export function isIOS(): boolean {
  const ua = navigator.userAgent;
  return /iP(hone|ad|od)/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}

type Sink = (chunk: Blob) => void;

/** Тайминги старта в консоль: localStorage["pomnyu.micdebug"] = "1". Нужны для разбора «обрезанного начала» на телефонах. */
const DEBUG = (() => {
  try {
    return localStorage.getItem("pomnyu.micdebug") === "1";
  } catch {
    return false;
  }
})();
const t0 = () => performance.now();
export const dbg = (...a: unknown[]) => DEBUG && console.info("[mic]", Math.round(performance.now()), ...a);

class MicEngine {
  /** Держать ли поток открытым между записями (везде, кроме iOS). */
  readonly keepWarm = !isIOS();
  level = 0;

  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private ctx: AudioContext | null = null;
  private graph: Promise<{ ctx: AudioContext; input: AudioNode }> | null = null;
  private opening: Promise<void> | null = null;
  private ring = new Int16Array((TARGET_RATE * PREROLL_MS) / 1000);
  private ringPos = 0;
  private ringFilled = 0;
  private lastFrameAt = 0;
  private frameWaiters: (() => void)[] = [];

  private sink: Sink | null = null;
  private batch: Int16Array[] = [];
  private batchSamples = 0;
  private postroll: { left: number; done: () => void } | null = null;
  private ending: Promise<void> | null = null;
  private idleTimer = 0;
  private listeners = new Set<() => void>();

  get warm(): boolean {
    return !!this.stream && this.stream.getAudioTracks().some((t) => t.readyState === "live");
  }

  onChange(f: () => void) {
    this.listeners.add(f);
    return () => void this.listeners.delete(f);
  }
  private emit() {
    this.listeners.forEach((f) => f());
  }

  /** Открыть микрофон (без записи). Безопасно вызывать повторно. */
  open(): Promise<void> {
    if (this.warm && this.ctx) return this.resume();
    if (this.opening) return this.opening;
    this.opening = this.doOpen().finally(() => (this.opening = null));
    return this.opening;
  }

  private async doOpen() {
    const AC: typeof AudioContext | undefined =
      window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC || !navigator.mediaDevices?.getUserMedia) throw "unsupported" satisfies EngineError;
    // Граф (AudioContext + worklet) и поток с микрофона готовим параллельно: на холодном старте
    // (iOS) это экономит сотни миллисекунд. Граф живёт между записями, гасится только поток.
    const started = t0();
    const graph = this.graph ?? this.buildGraph(AC);
    this.graph = graph;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (e) {
      const name = (e as DOMException).name;
      throw (name === "NotAllowedError" || name === "SecurityError" ? "denied" : "error") satisfies EngineError;
    }
    dbg("getUserMedia", Math.round(t0() - started), "ms");
    const { ctx, input } = await graph;
    dbg("graph ready", Math.round(t0() - started), "ms");
    this.source = ctx.createMediaStreamSource(stream);
    this.source.connect(input);
    stream.getAudioTracks().forEach((t) => (t.onended = () => this.releaseStream()));
    this.stream = stream;
    this.ctx = ctx;
    await this.resume();
    this.touch();
    this.emit();
  }

  private async buildGraph(AC: typeof AudioContext): Promise<{ ctx: AudioContext; input: AudioNode }> {
    const ctx = new AC();
    const mute = ctx.createGain();
    mute.gain.value = 0; // Safari обрабатывает узел, только если граф доходит до destination
    mute.connect(ctx.destination);
    if (ctx.audioWorklet) {
      await ctx.audioWorklet.addModule(WORKLET_URL);
      const node = new AudioWorkletNode(ctx, "pcm-downsampler");
      node.port.onmessage = (e) => this.onFrame(new Int16Array(e.data as ArrayBuffer));
      node.connect(mute);
      return { ctx, input: node };
    }
    return { ctx, input: this.scriptProcessorFallback(ctx, mute) };
  }

  /** Старые браузеры без AudioWorklet: тот же даунсэмплинг на главном потоке. */
  private scriptProcessorFallback(ctx: AudioContext, out: AudioNode): AudioNode {
    const sp = ctx.createScriptProcessor(2048, 1, 1);
    const ratio = ctx.sampleRate / TARGET_RATE;
    let acc = 0,
      n = 0,
      pos = 0,
      frame = new Int16Array(FRAME_SAMPLES),
      k = 0;
    sp.onaudioprocess = (e) => {
      const ch = e.inputBuffer.getChannelData(0);
      for (let i = 0; i < ch.length; i++) {
        acc += ch[i];
        n++;
        pos++;
        if (pos >= ratio) {
          pos -= ratio;
          const v = Math.max(-1, Math.min(1, acc / n));
          acc = n = 0;
          frame[k++] = v < 0 ? v * 0x8000 : v * 0x7fff;
          if (k === FRAME_SAMPLES) {
            this.onFrame(frame);
            frame = new Int16Array(FRAME_SAMPLES);
            k = 0;
          }
        }
      }
    };
    sp.connect(out);
    return sp;
  }

  /** Собрать граф заранее, без доступа к микрофону (на первом жесте: iOS разрешает AudioContext только так). */
  prepare() {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!this.graph && AC) {
      this.graph = this.buildGraph(AC);
      this.graph.then(({ ctx }) => (this.ctx ??= ctx)).catch(() => (this.graph = null));
    }
    return this.resume();
  }

  /** AudioContext без жеста пользователя стартует «suspended» — будим на любом нажатии. */
  async resume() {
    if (this.ctx && this.ctx.state !== "running") {
      try {
        await this.ctx.resume();
      } catch {
        /* будет разбужен следующим жестом */
      }
    }
  }

  /** Выключить микрофон, оставив граф: следующее открытие быстрее. */
  releaseStream() {
    clearTimeout(this.idleTimer);
    this.source?.disconnect();
    this.source = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.ringFilled = 0;
    this.level = 0;
    this.emit();
  }

  /** Выключить всё (вкладку свернули, доступ отозван). */
  release() {
    this.releaseStream();
    this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.graph = null;
  }

  /** Сколько держать поток после записи: на iOS — недолго, только на ответ в диалоге («в три», «да»). */
  private touch() {
    clearTimeout(this.idleTimer);
    const ms = this.keepWarm ? IDLE_RELEASE_MS : IOS_LINGER_MS;
    this.idleTimer = window.setTimeout(() => !this.sink && this.releaseStream(), ms);
  }

  private onFrame(f: Int16Array) {
    this.lastFrameAt = performance.now();
    // уровень для индикатора
    let sum = 0;
    for (let i = 0; i < f.length; i++) sum += (f[i] / 32768) ** 2;
    this.level = this.level * 0.6 + Math.min(1, Math.sqrt(sum / f.length) * 4) * 0.4;

    if (this.sink) {
      this.batch.push(f);
      this.batchSamples += f.length;
      if (this.postroll) {
        this.postroll.left -= f.length;
        if (this.postroll.left <= 0) {
          const done = this.postroll.done;
          this.postroll = null;
          this.flush();
          this.sink = null;
          done();
        }
      } else if (this.batchSamples >= SEND_EVERY_SAMPLES) this.flush();
    } else {
      // кольцевой буфер последних PREROLL_MS
      for (let i = 0; i < f.length; i++) {
        this.ring[this.ringPos] = f[i];
        this.ringPos = (this.ringPos + 1) % this.ring.length;
      }
      this.ringFilled = Math.min(this.ring.length, this.ringFilled + f.length);
    }
    const w = this.frameWaiters;
    this.frameWaiters = [];
    w.forEach((r) => r());
  }

  private flush() {
    if (!this.sink || !this.batchSamples) return;
    this.sink(new Blob(this.batch as BlobPart[], { type: "application/octet-stream" }));
    this.batch = [];
    this.batchSamples = 0;
  }

  private takeRing(): Int16Array {
    const n = this.ringFilled;
    const out = new Int16Array(n);
    const start = (this.ringPos - n + this.ring.length) % this.ring.length;
    for (let i = 0; i < n; i++) out[i] = this.ring[(start + i) % this.ring.length];
    this.ringFilled = 0;
    return out;
  }

  /** Кадры реально идут (контекст разбужен, микрофон отдаёт звук). */
  private framesFlowing(): Promise<void> {
    if (performance.now() - this.lastFrameAt < 100) return Promise.resolve();
    return new Promise((r) => this.frameWaiters.push(r));
  }

  /**
   * Начать запись: сразу отдаёт заголовок WAV и pre-roll, дальше — кадры по ходу записи.
   * Возвращает, сколько миллисекунд pre-roll ушло (0 — микрофон был выключен, холодный старт).
   * Резолвится, когда пошли реальные кадры: только тогда UI показывает «Говорите…».
   */
  async begin(sink: Sink): Promise<number> {
    dbg("begin");
    // нажали снова, пока дописывался хвост прошлой фразы — дождёмся его
    if (this.ending) await this.ending;
    await this.open();
    if (this.sink) throw new Error("уже идёт запись");
    clearTimeout(this.idleTimer);
    this.postroll = null;
    this.batch = [];
    this.batchSamples = 0;
    const pre = this.takeRing();
    this.sink = sink;
    sink(new Blob([streamingWavHeader(), pre as BlobPart], { type: MIME }));
    const waitFrom = t0();
    await this.framesFlowing();
    dbg("begin: pre-roll", pre.length / 16, "ms, first frame after", Math.round(t0() - waitFrom), "ms, ctx", this.ctx?.state);
    return Math.round((pre.length / TARGET_RATE) * 1000);
  }

  /** Закончить: дописать POSTROLL_MS после отпускания и только потом отдать управление. */
  end(): Promise<void> {
    if (this.ending) return this.ending;
    if (!this.sink) return Promise.resolve();
    this.ending = new Promise<void>((resolve) => {
      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        this.ending = null;
        this.touch();
        resolve();
      };
      this.postroll = { left: (TARGET_RATE * POSTROLL_MS) / 1000, done };
      // кадры перестали идти (вкладку свернули, микрофон отключили) — не ждём вечно
      window.setTimeout(() => {
        if (finished) return;
        this.postroll = null;
        this.flush();
        this.sink = null;
        done();
      }, POSTROLL_MS + 600);
    });
    return this.ending;
  }

  /** Отменить запись без отправки остатка. */
  cancel() {
    this.sink = null;
    this.postroll = null;
    this.batch = [];
    this.batchSamples = 0;
    this.touch();
  }

  get recording() {
    return this.sink !== null;
  }
}

export const micEngine = new MicEngine();

// Любой первый жест на странице (в том числе выбор пользователя на экране входа) собирает граф
// и будит AudioContext — к первому нажатию на кнопку остаётся только открыть микрофон.
if (typeof document !== "undefined") {
  const wake = () => void micEngine.prepare();
  document.addEventListener("pointerdown", wake, true);
  document.addEventListener("keydown", wake, true);
}
