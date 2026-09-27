// Speech input for the Log screen, kept pure so it can be checked without a
// browser. The words still go to the Pi's parser and its echo-and-confirm
// (invariant 2); nothing here decides what is saved.

/** Why this browser cannot do speech, in plain words, or null when it can try. */
export function speechUnavailable(env: { hasApi: boolean; secure: boolean; ua: string }): string | null {
  if (/CriOS|FxiOS|EdgiOS|OPiOS/.test(env.ua))
    return "This browser can't do speech on iPhone (only Safari can). Tap the text box and use your keyboard's microphone.";
  if (!env.secure) return "Speech needs a secure (https) page. Tap the text box and use your keyboard's microphone.";
  if (!env.hasApi) return "This browser can't do speech; tap the text box and use your keyboard's microphone.";
  return null;
}

const IOS = (ua: string) => /iPhone|iPad|iPod/.test(ua);

/** A SpeechRecognition error code as something a person can act on. */
export function speechErrorText(code: string, ua: string): string {
  switch (code) {
    case "not-allowed":
      return IOS(ua)
        ? "Microphone blocked: allow it in Settings > Safari > Microphone (or tap aA > Website Settings), then try again."
        : "Microphone blocked: allow it for this site in your browser's site settings, then try again.";
    case "service-not-allowed":
      return IOS(ua)
        ? "Speech is off on this iPhone: turn on Settings > Siri (or General > Keyboard > Enable Dictation). Or tap the text box and use your keyboard's microphone."
        : "This browser won't run speech here; tap the text box and use your keyboard's microphone.";
    case "no-speech":
      return "Didn't hear anything. Tap and speak again, or type it.";
    case "audio-capture":
      return "No microphone found (or another app is using it). Tap the text box and type instead.";
    case "network":
      return "Speech needs the internet and couldn't reach it. Tap the text box and use your keyboard's microphone.";
    case "language-not-supported":
      return "Speech isn't available in this language here; tap the text box and type instead.";
    case "aborted":
      return "Stopped listening. Tap and speak again.";
    default:
      return `Speech failed (${code}). Tap the text box and use your keyboard's microphone.`;
  }
}

/** Errors that will repeat on every tap: stop offering the speech button. */
export const permanentSpeechError = (code: string) =>
  code === "not-allowed" || code === "service-not-allowed" || code === "language-not-supported";

const ONES: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19,
};
const TENS: Record<string, number> = {
  twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90,
};

/** Recognizers (Safari especially) often spell small numbers out: "four
 * units", "twenty-five carbs". The Pi's parser reads digits only, so turn
 * number words (0-99, "and a half") into digits; everything else is kept. */
export function numberWordsToDigits(text: string): string {
  const half = "(?:\\s+and\\s+a\\s+half)?";
  const word = `(${Object.keys(TENS).join("|")})(?:[\\s-]+(${Object.keys(ONES).filter((w) => ONES[w] > 0 && ONES[w] < 10).join("|")}))?|(${Object.keys(ONES).join("|")})`;
  const re = new RegExp(`\\b(?:${word})\\b(${half})`, "gi");
  return text.replace(re, (_m, tens?: string, unit?: string, one?: string, andHalf?: string) => {
    const n = one ? ONES[one.toLowerCase()] : TENS[tens!.toLowerCase()] + (unit ? ONES[unit.toLowerCase()] : 0);
    return andHalf ? `${n + 0.5}` : `${n}`;
  });
}
