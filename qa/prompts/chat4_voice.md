# Промпт для чата 4 (голос)

```
От чата 7 (QA голоса), 22.10. Две мелочи и одна просьба по наблюдаемости.

QA-08 · normalize_for_wer по-разному считает «15 00» и «15:00»
  backend/app/voice/text_norm.py: normalize_for_wer("в 15:00") = «в пятнадцать ноль ноль»,
  normalize_for_wer("в 15 00") = «в пятнадцать ноль» (вторые «00» → одно «ноль»). GigaAM e2e иногда
  пишет время через пробел — бенчмарк засчитает это как ошибку слова.
  Воспроизведение: python -c "from app.voice.text_norm import normalize_for_wer as n; print(n('в 15 00'), '|', n('в 15:00'))"
  Починено: обе строки нормализуются одинаково (и «15.00» тоже); тест в tests/voice/test_text_norm.py.
  У себя в backend/eval/asr/wer.py я это обхожу заменой «HH MM» → «HH:MM» до нормализации; после твоей
  правки обход можно оставить — он не мешает.

QA-11 · какой вариант модели работает, не видно снаружи
  Для сравнения int8 и fp32 мне нужно знать по ответу сервера, какая сборка отвечает. Сейчас /health даёт
  stt: ok и impl.stt: gigaam, а reply.debug.voice — stt_provider, audio_s, decode_ms, stt_ms.
  Просьба: добавить в debug.voice (и в /health impl) модель и квантование, например
  "stt_model": "v3_e2e_rnnt", "quantize": "int8". qa/run_voice.py сохранит их в results.jsonl.
  Починено: в reply.debug.voice есть stt_model и quantize; tests/voice/test_ws_voice.py проверяет поля.

Для информации: прогон записей и сравнение int8 ↔ fp32 делаю я (qa/ab_quant.py собирает образ дважды
с GIGAAM_QUANTIZE=none / int8 через docker compose локально, на стенде — ничего тяжёлого). Если у тебя
есть зафиксированные наблюдения, как e2e_rnnt пишет время («15:00», «15 00», «пятнадцать»), имена
с маленькой буквы и «без пятнадцати десять» — пришли пару примеров, сверю с записями.
```
