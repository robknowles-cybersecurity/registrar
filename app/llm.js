// Optional "use my own API key" seam. OFF by default.
//
// What leaves the browser when it is on, and only when the person presses the
// button: the TEXT already extracted from the certificate (never the file,
// never the ledger), sent to the provider the person chose, with the key they
// typed. The key lives in this module's memory for the session only: it is
// never written to IndexedDB, localStorage, a cookie or an export, and a
// reload forgets it.
//
// Plain fetch rather than a provider SDK: the app ships no runtime
// dependencies beyond the vendored PDF and OCR libraries.

let session = null; // {provider, key, model, baseUrl}

export const PROVIDERS = {
  anthropic: { label: "Anthropic", defaultModel: "claude-opus-5-5", baseUrl: "https://api.anthropic.com" },
  openai: { label: "OpenAI-compatible", defaultModel: "", baseUrl: "https://api.openai.com/v1" },
};

export function configure({ provider, key, model, baseUrl }) {
  if (!PROVIDERS[provider]) throw new Error(`unknown provider ${provider}`);
  if (!key || !String(key).trim()) throw new Error("an API key is required");
  session = {
    provider,
    key: String(key).trim(),
    model: (model || PROVIDERS[provider].defaultModel || "").trim(),
    baseUrl: (baseUrl || PROVIDERS[provider].baseUrl).replace(/\/+$/, ""),
  };
  if (!session.model) throw new Error("a model name is required");
}

export function forget() {
  session = null;
}

export function status() {
  return session ? { on: true, provider: session.provider, model: session.model, baseUrl: session.baseUrl } : { on: false };
}

const FIELDS = ["activity_date", "hours", "provider", "title", "holder_name"];

export function buildPrompt(text) {
  return [
    "Extract these fields from the text of a continuing-education certificate.",
    "Return ONLY a JSON object with exactly these keys:",
    '  "activity_date": the completion or event date as YYYY-MM-DD (for a date range, the last day; never an expiry date), or null',
    '  "hours": the number of hours or CPE/CEU/PDU credits awarded, as a number (prefer contact hours over 10-hour CEUs), or null',
    '  "provider": the organisation that ran or issued the activity, or null',
    '  "title": the course, event or session title, or null',
    '  "holder_name": the name of the person the certificate is for, or null',
    "Use null when the text does not say. Do not guess.",
    "",
    "Certificate text:",
    "<<<",
    text.slice(0, 20000),
    ">>>",
  ].join("\n");
}

export function parseReply(reply) {
  const m = /\{[\s\S]*\}/.exec(reply || "");
  if (!m) throw new Error("the model did not return JSON");
  const obj = JSON.parse(m[0]);
  const out = {};
  for (const f of FIELDS) {
    let v = obj[f];
    if (v === undefined || v === "") v = null;
    if (f === "hours" && v != null) v = Number(v);
    if (f === "hours" && !Number.isFinite(v)) v = null;
    if (f === "activity_date" && v != null && !/^\d{4}-\d{2}-\d{2}$/.test(String(v))) v = null;
    out[f] = v;
  }
  return out;
}

/** Send extracted text (not the file) to the configured provider. */
export async function extractWithLlm(text, { fetchImpl = fetch } = {}) {
  if (!session) throw new Error("no API key set for this session");
  const prompt = buildPrompt(String(text || ""));
  let reply;
  if (session.provider === "anthropic") {
    const res = await fetchImpl(`${session.baseUrl}/v1/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": session.key,
        "anthropic-version": "2026-10-21",
        "anthropic-dangerous-direct-browser-access": "true",
        "anthropic-beta": "server-side-fallback-2026-07-01",
      },
      body: JSON.stringify({
        model: session.model,
        max_tokens: 2048,
        output_config: { effort: "low" },
        fallbacks: "default",
        messages: [{ role: "user", content: prompt }],
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`Anthropic API ${res.status}: ${(data.error && data.error.message) || res.statusText}`);
    if (data.stop_reason === "refusal") throw new Error("the model declined this request");
    reply = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
  } else {
    const res = await fetchImpl(`${session.baseUrl}/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${session.key}` },
      body: JSON.stringify({ model: session.model, messages: [{ role: "user", content: prompt }] }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`API ${res.status}: ${(data.error && data.error.message) || res.statusText}`);
    reply = (((data.choices || [])[0] || {}).message || {}).content || "";
  }
  return parseReply(reply);
}
