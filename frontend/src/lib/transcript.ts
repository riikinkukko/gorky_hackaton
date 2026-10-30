/** Короткие слова, с которых фраза вправду может начинаться: «в 10…», «на завтра…», «а на неделе?». */
const SHORT_WORDS = new Set("а в во и к ко на не ну о об от по с со у я да до ок мы вы он".split(" "));

/** Транскрипт начинается со слога, а не со слова (меньше 3 букв перед пробелом) — вероятно, обрезано начало. */
export function looksClipped(text: string): boolean {
  const first = text.trim().split(/\s+/)[0]?.toLowerCase().replace(/[^а-яёa-z]/g, "") ?? "";
  return first.length > 0 && first.length < 3 && !SHORT_WORDS.has(first);
}
