"""Прогон записей через WebSocket /ws/voice: эталон, транскрипт, WER/CER, команда, класс ошибки.

    cd backend && .venv/bin/python ../qa/run_voice.py --base http://localhost:8080 \\
        --manifest ../qa/audio/manifest.csv --name local-int8

Что делает для каждой строки манифеста:
1. «Текстовый эталон»: эталонная фраза (query из golden_eval) уходит в POST /api/chat в новой
   сессии — так видно, что ассистент делает при идеальном распознавании. Кэшируется по qid.
2. «Голос»: файл уходит в /ws/voice так же, как его шлёт фронт: start{mime} → чанки по 250 мс
   → end. Приходят финальный транскрипт и reply.
3. После каждого изменяющего ответа — POST /api/undo, чтобы прогон не оставлял записей.
4. Сравнение: голос ≡ текст (тот же intent, action, те же записи) и, если «сейчас» на сервере
   совпадает с now эталона, — проверки golden_eval (eval.run_eval.score, как в make eval).

Классы (docs: qa/README.md):
  A — распознавание: транскрипт неверный, и из-за этого голос ≠ текст;
  B — понимание: транскрипт верный (или ошибка не мешает), а команда не та;
  C — диалог/ответ: команда верная, а ответ не тот (поиск, список, конфликт, правка);
  D — запись/фронт: пустой транскрипт, обрезаны края фразы, сервер получил короче файла.

Источники аудио:
  по умолчанию        файл как есть (webm/ogg/mp4/wav), чанками;
  --postroll 0.4      файл → PCM 16 кГц + 0,4 с тишины → WAV (симуляция post-roll на фронте);
  --as-wav            то же без тишины — контроль, что разница не от смены контейнера;
  --hyp-csv FILE      вместо аудио — готовые транскрипты (колонки file|qid, hyp): кадр
                      «text:…», который понимает только заглушка STT_PROVIDER=fake.
                      Так проверяется устойчивость понимания к ошибкам распознавания.

Стенд (--stand): строго по одной фразе, пауза ≥3 с между запросами, не больше 25 строк и по
умолчанию только чтение (list/search): пользователь 1 на стенде — демо для жюри. Изменяющие
фразы — только с --allow-writes (каждая откатывается через /api/undo). Сервер общий с другим
приложением — никаких параллельных прогонов. На стенде «сейчас» настоящее (X-Freeze-Now в prod
запрещён), поэтому проверки golden_eval выключаются и остаётся сравнение «голос ≡ текст».
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
import websockets  # noqa: E402

from app.contracts import AssistantReply  # noqa: E402
from eval.asr.wer import align, cer, corpus, entity_hits  # noqa: E402
from eval.metrics import norm_title  # noqa: E402
from eval.run_eval import Report, score  # noqa: E402

GOLDEN = ROOT / "backend/eval/golden_eval.jsonl"
PHRASES = ROOT / "qa/phrases.jsonl"
STAND = "voice.planimetrika.online"
MIME_BY_EXT = {
    ".webm": "audio/webm;codecs=opus",
    ".ogg": "audio/ogg;codecs=opus",
    ".opus": "audio/ogg;codecs=opus",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
}
MUTATING = {"created", "updated", "completed", "deleted"}
NOT_HEARD = ("Не расслышал", "Не получилось распознать")
READ_ONLY_CATEGORIES = {"list", "search", "smalltalk"}
CSV_MAX_ID = 1000  # id из CSV организаторов; новые записи получают id больше


# ---------- данные ----------


@dataclass
class Row:
    file: str
    qid: str
    ref: str  # что человек читал
    speaker: str = ""
    device: str = ""  # phone | laptop | headset
    env: str = ""  # quiet | noise
    mode: str = ""  # app | clean (как писал рекордер)
    hyp_override: str | None = None  # --hyp-csv


@dataclass
class Turn:
    transcript: str = ""
    reply: AssistantReply | None = None
    error: str | None = None
    audio_s: float | None = None
    stt_ms: float | None = None
    total_ms: float = 0.0
    undone: bool = False


@dataclass
class Result:
    file: str
    qid: str
    speaker: str
    device: str
    env: str
    mode: str
    ref: str
    hyp: str
    wer: float
    cer: float
    diff: str
    ent_hit: int
    ent_total: int
    file_s: float | None
    audio_s: float | None
    stt_ms: float | None
    total_ms: float
    intent_voice: str | None
    intent_text: str | None
    action_voice: str | None
    action_text: str | None
    parity: bool
    golden_checks: dict[str, bool] = field(default_factory=dict)
    golden_ok: bool | None = None  # текстовый эталон прошёл golden (None — не проверялся)
    voice_golden_ok: bool | None = None
    cls: str = "OK"  # OK | A | B | C | D
    why: str = ""
    reply_voice: str = ""
    reply_text: str = ""


# ---------- аудио ----------


def ffprobe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip()  # fmt: skip
        return float(out) if out and out != "N/A" else _decoded_duration(path)
    except (subprocess.SubprocessError, ValueError):
        return _decoded_duration(path)


def _decoded_duration(path: Path) -> float | None:
    """У webm из MediaRecorder в заголовке нет длительности — считаем по PCM."""
    try:
        pcm = to_pcm16k(path)
    except RuntimeError:
        return None
    return len(pcm) / 2 / 16000


def to_pcm16k(path: Path) -> bytes:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("нужен ffmpeg для --postroll / --as-wav")
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, timeout=60,
    )  # fmt: skip
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="ignore")[-300:])
    return p.stdout


def wav_bytes(pcm: bytes, rate: int = 16000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def payload_for(
    row: Row, audio_dir: Path, a: argparse.Namespace
) -> tuple[bytes, str, float | None]:
    """→ (байты, mime, длительность исходного файла)."""
    if row.hyp_override is not None:
        return b"text:" + row.hyp_override.encode(), "text/plain", None
    path = audio_dir / row.file
    dur = ffprobe_duration(path)
    if a.postroll or a.as_wav:
        pcm = to_pcm16k(path) + b"\x00\x00" * int(16000 * (a.postroll or 0))
        return wav_bytes(pcm), "audio/wav", dur
    return path.read_bytes(), MIME_BY_EXT.get(path.suffix.lower(), "audio/webm;codecs=opus"), dur


def chunks(data: bytes, dur: float | None, chunk_ms: int) -> list[bytes]:
    """Режем на столько кусков, сколько 250-мс чанков отдал бы MediaRecorder."""
    n = max(1, math.ceil((dur or 1.0) * 1000 / chunk_ms))
    size = max(1, math.ceil(len(data) / n))
    return [data[i : i + size] for i in range(0, len(data), size)]


# ---------- сервер ----------


class Client:
    def __init__(self, base: str, user_id: int, pace_s: float) -> None:
        self.base = base.rstrip("/")
        self.ws_base = self.base.replace("https://", "wss://").replace("http://", "ws://")
        self.user_id = user_id
        self.pace_s = pace_s
        self.http = httpx.AsyncClient(timeout=60, headers={"X-User-Id": str(user_id)})
        self.cookie: str | None = None
        self._last = 0.0

    async def pace(self) -> None:
        wait = self._last + self.pace_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def login(self) -> bool:
        """SEC-01: подписанная cookie (POST /api/session). Cookie помечена secure, поэтому httpx не
        вернёт её по http://localhost сам — берём значение из Set-Cookie и шлём заголовком Cookie
        и в REST, и в WebSocket. Старый сервер без /api/session — работаем по X-User-Id."""
        r = await self.http.post(f"{self.base}/api/session", json={"user_id": self.user_id})
        if r.status_code != 200:
            return False
        for raw in r.headers.get_list("set-cookie"):
            name, _, rest = raw.partition("=")
            if name.strip() == "pomnyu_session":
                self.cookie = f"pomnyu_session={rest.split(';', 1)[0]}"
                self.http.headers["Cookie"] = self.cookie
                return True
        return False

    async def health(self) -> dict:
        r = await self.http.get(f"{self.base}/health")
        r.raise_for_status()
        return r.json()

    async def chat(self, text: str, session: str) -> AssistantReply:
        await self.pace()
        r = await self.http.post(
            f"{self.base}/api/chat", json={"text": text, "session_id": session}
        )
        r.raise_for_status()
        return AssistantReply.model_validate(r.json())

    async def undo(self, session: str) -> None:
        await self.pace()
        r = await self.http.post(f"{self.base}/api/undo", json={"session_id": session})
        r.raise_for_status()

    async def voice(
        self, data: bytes, mime: str, dur: float | None, session: str, a: argparse.Namespace
    ) -> Turn:
        await self.pace()
        url = f"{self.ws_base}/ws/voice?session_id={session}&user_id={self.user_id}"
        t = Turn()
        t0 = time.perf_counter()
        hdrs = {"Cookie": self.cookie} if self.cookie else None
        async with websockets.connect(
            url, max_size=8 * 2**20, open_timeout=20, additional_headers=hdrs
        ) as ws:
            await ws.send(json.dumps({"type": "start", "mime": mime}))
            parts = [data] if mime == "text/plain" else chunks(data, dur, a.chunk_ms)
            for part in parts:
                await ws.send(part)
                if a.realtime:
                    await asyncio.sleep(a.chunk_ms / 1000)
            await ws.send(json.dumps({"type": "end"}))
            deadline = time.monotonic() + a.timeout
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                except TimeoutError:
                    break
                if isinstance(msg, bytes):
                    continue  # озвучка ответа
                m = json.loads(msg)
                if m.get("type") == "transcript" and m.get("final"):
                    t.transcript = m.get("text") or ""
                elif m.get("type") == "error":
                    t.error = m.get("message")
                elif m.get("type") == "reply":
                    m.pop("type", None)
                    m.pop("audio_mime", None)
                    t.reply = AssistantReply.model_validate(m)
                    v = (t.reply.debug or {}).get("voice") or {}
                    t.audio_s, t.stt_ms = v.get("audio_s"), v.get("stt_ms")
                    break
        t.total_ms = (time.perf_counter() - t0) * 1000
        if t.reply is None and t.error is None:
            t.error = f"нет reply за {a.timeout} с"
        return t


# ---------- сравнение ----------


def ranked_ids(reply: AssistantReply | None) -> list[int]:
    """id для score(): записи из CSV — как есть, новые — со знаком минус (как в run_eval)."""
    if reply is None:
        return []
    return [i.id if 0 < i.id <= CSV_MAX_ID else -i.id for i in reply.items]


def _item_sig(it: Any) -> tuple:
    if 0 < it.id <= CSV_MAX_ID:
        return ("id", it.id)
    at = it.starts_at or it.due_at
    return ("new", it.type.value, norm_title(it.title), at.isoformat()[:16] if at else None)


def signature(reply: AssistantReply | None) -> tuple:
    if reply is None:
        return (None, None, ())
    d = reply.debug or {}
    return (d.get("intent"), reply.action, tuple(_item_sig(i) for i in reply.items[:3]))


def intent_of(reply: AssistantReply | None) -> str | None:
    return (reply.debug or {}).get("intent") if reply else None


B_CHECKS = {"intent", "date", "slots"}  # что считает NLU (интент, даты, слоты)


IGNORABLE = {"ноль", "э", "ээ", "эээ", "ну", "мм", "а"}  # не делают транскрипт «неверным»


def classify(
    r: Result, golden_ref_checks: dict[str, bool], empty: bool, short: bool, audio: bool = True
) -> None:
    al = align(r.ref, r.hyp)
    al.ops = [
        op
        for op in al.ops
        if not (op[0] == "del" and op[1] in IGNORABLE)
        and not (op[0] == "ins" and op[2] in IGNORABLE)
    ]
    if empty:
        r.cls, r.why = "D", "пустой транскрипт / «не расслышал»"
        return
    if not r.parity:
        if audio and al.errors and al.only_edge_errors():
            h, t = al.edge_deletions()
            r.cls, r.why = "D", f"обрезано: начало −{h}, конец −{t} слов"
        elif short:
            r.cls, r.why = "D", f"сервер получил {r.audio_s} с из {r.file_s:.2f} с"
        elif al.errors:
            r.cls, r.why = "A", al.diff()
        else:
            r.cls = "B"
            r.why = f"транскрипт верный, ответ другой: «{r.reply_voice[:70]}»"
        return
    # голос ≡ текст: дальше смотрим, прав ли сам ответ по golden_eval
    if r.golden_ok is False:
        failed = {k for k, v in golden_ref_checks.items() if not v}
        r.cls = "B" if failed & B_CHECKS else "C"
        r.why = "не прошли: " + ", ".join(sorted(failed))
        if al.errors:
            r.why += f" (+ASR: {al.diff()})"
        return
    r.cls, r.why = "OK", (f"ASR-ошибка не помешала: {al.diff()}" if al.errors else "")


# ---------- прогон ----------


def load_golden() -> dict[str, dict]:
    return {
        (r := json.loads(line))["qid"]: r
        for line in GOLDEN.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def load_rows(a: argparse.Namespace, golden: dict[str, dict]) -> list[Row]:
    rows: list[Row] = []
    phrases = {}
    if PHRASES.exists():
        for line in PHRASES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                p = json.loads(line)
                phrases[p["qid"]] = p
    if a.hyp_csv:
        with open(a.hyp_csv, encoding="utf-8-sig", newline="") as f:
            for d in csv.DictReader(f):
                qid = d["qid"]
                rows.append(
                    Row(
                        file=d.get("file") or f"{d.get('variant', 'sim')}__{qid}",
                        qid=qid,
                        ref=d.get("ref") or phrases.get(qid, {}).get("say") or golden[qid]["query"],
                        speaker=d.get("speaker", "sim"),
                        device=d.get("device", "-"),
                        env=d.get("env") or d.get("variant", "sim"),
                        mode="text",
                        hyp_override=d["hyp"],
                    )
                )
    else:
        with open(a.manifest, encoding="utf-8-sig", newline="") as f:
            for d in csv.DictReader(f):
                if not d.get("file") or d.get("skip", "").strip():
                    continue
                qid = d["qid"]
                rows.append(
                    Row(
                        file=d["file"],
                        qid=qid,
                        ref=d.get("ref") or phrases.get(qid, {}).get("say") or golden[qid]["query"],
                        speaker=d.get("speaker", ""),
                        device=d.get("device", ""),
                        env=d.get("env", ""),
                        mode=d.get("mode", ""),
                    )
                )
    if a.qids:
        keep = set(a.qids.split(","))
        rows = [r for r in rows if r.qid in keep]
    if a.speakers:
        keep = set(a.speakers.split(","))
        rows = [r for r in rows if r.speaker in keep]
    missing = sorted({r.qid for r in rows} - set(golden))
    if missing:
        sys.exit(f"нет в golden_eval: {missing}")
    return rows[: a.limit] if a.limit else rows


async def text_reference(cl: Client, g: dict, cache: dict[str, Any], a: argparse.Namespace) -> Any:
    if g["qid"] in cache:
        return cache[g["qid"]]
    session = f"qa-ref-{uuid.uuid4().hex[:10]}"
    for h in g.get("history", []):
        await cl.chat(h, session)
    reply = await cl.chat(g["query"], session)
    if reply.action in MUTATING and not a.no_undo:
        await cl.undo(session)
    cache[g["qid"]] = reply
    return reply


async def run(a: argparse.Namespace) -> Path:
    golden = load_golden()
    rows = load_rows(a, golden)
    if a.stand and not a.allow_writes:
        # на стенде пользователь 1 — демо для жюри: по умолчанию только чтение
        before = len(rows)
        rows = [r for r in rows if golden[r.qid]["category"] in READ_ONLY_CATEGORIES]
        if len(rows) < before:
            print(
                f"--stand: пропущено {before - len(rows)} изменяющих фраз (--allow-writes)",
                file=sys.stderr,
            )
    cl = Client(a.base, a.user_id, a.pace)
    health = await cl.health()
    print(f"вход: {'cookie' if await cl.login() else 'X-User-Id'}", file=sys.stderr)
    impl = health.get("impl", {})
    if a.hyp_csv and impl.get("stt") != "fake":
        sys.exit("--hyp-csv работает только с STT_PROVIDER=fake (кадр «text:…»)")
    if not a.hyp_csv and impl.get("stt") == "fake" and not a.allow_fake:
        sys.exit("на сервере STT_PROVIDER=fake — аудио не распознается; нужен gigaam")
    now_ok = all(str(health.get("now", "")).startswith(g["now"][:16]) for g in golden.values())
    print(
        f"сервер: {impl}; now={health.get('now')}; "
        f"golden-проверки {'включены' if now_ok else 'выключены (now ≠ 2026-10-21 14:00)'}",
        file=sys.stderr,
    )

    audio_dir = Path(a.manifest).parent if a.manifest else Path(".")
    out = Path(a.out) / a.name
    out.mkdir(parents=True, exist_ok=True)
    ref_cache: dict[str, AssistantReply] = {}
    results: list[Result] = []

    for i, row in enumerate(rows, 1):
        g = golden[row.qid]
        ref_reply = await text_reference(cl, g, ref_cache, a)
        data, mime, file_s = payload_for(row, audio_dir, a)
        session = f"qa-{uuid.uuid4().hex[:10]}"
        for h in g.get("history", []):
            await cl.chat(h, session)
        try:
            t = await cl.voice(data, mime, file_s, session, a)
        except (OSError, websockets.WebSocketException) as exc:
            t = Turn(error=f"ws: {exc}")
        if t.reply and t.reply.action in MUTATING and not a.no_undo:
            await cl.undo(session)
            t.undone = True

        hyp = row.hyp_override if row.hyp_override is not None else t.transcript
        al = align(row.ref, hyp)
        ents = list((_phrase(row.qid) or {}).get("entities", []))
        eh, et = entity_hits(ents, hyp)

        g_ref: dict[str, bool] = {}
        g_ok = g_voice = None
        if now_ok:
            g_ref = score(g, ref_reply, ranked_ids(ref_reply), Report())["checks"]
            g_ok = all(g_ref.values()) if g_ref else None
            if t.reply is not None:
                cv = score(g, t.reply, ranked_ids(t.reply), Report())["checks"]
                g_voice = all(cv.values()) if cv else None

        empty = (not hyp.strip()) or bool(t.error and t.error.startswith(NOT_HEARD))
        short = bool(file_s and t.audio_s is not None and t.audio_s < file_s - 0.3)
        res = Result(
            file=row.file,
            qid=row.qid,
            speaker=row.speaker,
            device=row.device,
            env=row.env,
            mode=row.mode,
            ref=row.ref,
            hyp=hyp,
            wer=round(al.wer, 3),
            cer=round(cer(row.ref, hyp), 3),
            diff=al.diff(),
            ent_hit=eh,
            ent_total=et,
            file_s=round(file_s, 2) if file_s else None,
            audio_s=t.audio_s,
            stt_ms=t.stt_ms,
            total_ms=round(t.total_ms),
            intent_voice=intent_of(t.reply),
            intent_text=intent_of(ref_reply),
            action_voice=t.reply.action if t.reply else None,
            action_text=ref_reply.action,
            parity=t.reply is not None and signature(t.reply) == signature(ref_reply),
            golden_checks=g_ref,
            golden_ok=g_ok,
            voice_golden_ok=g_voice,
            reply_voice=(t.reply.text if t.reply else (t.error or ""))[:160],
            reply_text=ref_reply.text[:160],
        )
        classify(res, g_ref, empty, short, audio=row.hyp_override is None)
        results.append(res)
        mark = "✓" if res.cls == "OK" else res.cls
        print(
            f"[{i}/{len(rows)}] {mark} {row.qid} {row.file}  WER={res.wer:.2f}  "
            f"{res.intent_voice}/{res.action_voice}  {res.why[:90]}",
            file=sys.stderr,
        )

    write_outputs(out, results, a, health)
    await cl.http.aclose()
    return out


_PHRASE_CACHE: dict[str, dict] | None = None


def _phrase(qid: str) -> dict | None:
    global _PHRASE_CACHE
    if _PHRASE_CACHE is None:
        _PHRASE_CACHE = {}
        if PHRASES.exists():
            for line in PHRASES.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    p = json.loads(line)
                    _PHRASE_CACHE[p["qid"]] = p
    return _PHRASE_CACHE.get(qid)


# ---------- отчёт ----------


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_outputs(out: Path, results: list[Result], a: argparse.Namespace, health: dict) -> None:
    with open(out / "results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    cols = [
        "file", "qid", "speaker", "device", "env", "cls", "wer", "cer", "ref", "hyp", "why",
        "intent_voice", "action_voice", "intent_text", "action_text", "parity", "golden_ok",
        "audio_s", "file_s", "stt_ms", "total_ms",
    ]  # fmt: skip
    with open(out / "results.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", delimiter=";")
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))
    (out / "summary.md").write_text(render(results, a, health), encoding="utf-8")
    print(f"→ {out}/summary.md, results.csv, results.jsonl", file=sys.stderr)


def render(results: list[Result], a: argparse.Namespace, health: dict) -> str:
    n = len(results)
    if not n:
        return "Нет строк.\n"
    w, c = corpus([(r.ref, r.hyp) for r in results])
    eh = sum(r.ent_hit for r in results)
    et = sum(r.ent_total for r in results)
    ok = sum(r.cls == "OK" for r in results)
    par = sum(r.parity for r in results)
    src = (
        f"готовые транскрипты `{Path(a.hyp_csv).name}`"
        if a.hyp_csv
        else f"аудио `{a.manifest}`"
        + (f", post-roll {a.postroll} с (WAV)" if a.postroll else "")
        + (", WAV без post-roll" if a.as_wav and not a.postroll else "")
    )
    lines = [
        f"# Прогон голоса: {a.name}",
        "",
        f"{datetime.now():%Y-%m-%d %H:%M} · сервер `{a.base}` · реализация `{health.get('impl')}` "
        f"· now `{health.get('now')}` · источник: {src}",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Строк | {n} |",
        f"| WER / CER (корпусные) | {_pct(w)} / {_pct(c)} |",
        f"| Сущности узнаны (корень 4 буквы) | {eh}/{et} = {_pct(eh / max(et, 1))} |",
        f"| Голос ≡ текст (тот же intent, action, записи) | {par}/{n} = {_pct(par / n)} |",
        f"| Успех (≡ текст и текст прошёл golden) | {ok}/{n} = {_pct(ok / n)} |",
    ]
    stt = sorted(r.stt_ms for r in results if r.stt_ms is not None)
    if stt and stt[-1] > 0:
        lines.append(f"| STT p50 / max | {stt[len(stt) // 2]:.0f} / {stt[-1]:.0f} мс |")
    tot = sorted(r.total_ms for r in results)
    lines.append(f"| Конец записи → reply, p50 | {tot[len(tot) // 2]:.0f} мс |")

    lines += [
        "",
        "## Классы ошибок",
        "",
        "| Класс | Строк | Доля от ошибок | Владелец |",
        "|---|---|---|---|",
    ]
    owners = {
        "A": "чат 4 (модель/декодер), чат 1 (нормализация сущностей)",
        "B": "чат 1",
        "C": "чат 2 (диалог), чат 3 (поиск/данные)",
        "D": "чат 5 (фронт), чат 4 (ws_voice)",
    }
    errs = [r for r in results if r.cls != "OK"]
    for k in "ABCD":
        m = sum(r.cls == k for r in errs)
        lines.append(f"| {k} | {m} | {_pct(m / max(len(errs), 1))} | {owners[k]} |")

    groups: dict[str, list[Result]] = {}
    for r in results:
        key = " · ".join(x for x in (r.speaker, r.device, r.env) if x) or "-"
        groups.setdefault(key, []).append(r)
    if len(groups) > 1:
        lines += [
            "",
            "## По условиям",
            "",
            "| Условия | Строк | WER | Успех |",
            "|---|---|---|---|",
        ]
        for k, rs in sorted(groups.items()):
            gw, _ = corpus([(r.ref, r.hyp) for r in rs])
            share = sum(r.cls == "OK" for r in rs) / len(rs)
            lines.append(f"| {k} | {len(rs)} | {_pct(gw)} | {_pct(share)} |")

    lines += [
        "",
        "## Ошибки",
        "",
        "| Класс | qid | Файл | Эталон → транскрипт / причина | Голос | Текст |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(errs, key=lambda r: (r.cls, r.qid)):
        lines.append(
            f"| {r.cls} | {r.qid} | {r.file} | {r.why.replace('|', '/')} | "
            f"{r.intent_voice}/{r.action_voice} | {r.intent_text}/{r.action_text} |"
        )
    tolerated = [r for r in results if r.cls == "OK" and r.why]
    if tolerated:
        lines += ["", "## Ошибки распознавания, которые не помешали", ""]
        lines += [f"- {r.qid} {r.file}: {r.why}" for r in tolerated]
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base", default="http://localhost:8080")
    p.add_argument("--manifest", default=str(ROOT / "qa/audio/manifest.csv"))
    p.add_argument("--hyp-csv")
    p.add_argument("--name", default=f"run-{datetime.now():%m%d-%H%M}")
    p.add_argument("--out", default=str(ROOT / "qa/runs"))
    p.add_argument("--user-id", type=int, default=1)
    p.add_argument("--qids", help="через запятую")
    p.add_argument("--speakers", help="через запятую")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--postroll", type=float, default=0.0, help="секунд тишины в конец (WAV)")
    p.add_argument("--as-wav", action="store_true", help="WAV без тишины (контроль для --postroll)")
    p.add_argument("--chunk-ms", type=int, default=250)
    p.add_argument("--realtime", action="store_true", help="слать чанки с паузой chunk-ms")
    p.add_argument("--pace", type=float, default=0.0, help="минимум секунд между запросами")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--no-undo", action="store_true")
    p.add_argument("--allow-fake", action="store_true")
    p.add_argument(
        "--allow-writes",
        action="store_true",
        help="стенд: прогонять и изменяющие фразы (create/update/…, с откатом через undo)",
    )
    p.add_argument(
        "--stand", action="store_true", help="стенд: по одной фразе, пауза ≥3 с, ≤25 строк"
    )
    a = p.parse_args()
    if STAND in a.base and not a.stand:
        sys.exit("это стенд: добавьте --stand (по одной фразе, с паузами, не больше 25 строк)")
    if a.stand:
        a.pace = max(a.pace, 3.0)
        a.limit = min(a.limit or 25, 25)
        a.realtime = True  # как живой пользователь: не забиваем CPU пачкой
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
