// The BYOK seam: off by default, sends text only, never stores the key.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as LLM from "../src/llm.js";

test("off by default", () => {
  assert.deepEqual(LLM.status(), { on: false });
});

test("Anthropic request carries the text and nothing else of yours", async () => {
  LLM.configure({ provider: "anthropic", key: "sk-test-123" });
  let seen;
  const fakeFetch = async (url, init) => {
    seen = { url, init };
    return { ok: true, json: async () => ({ stop_reason: "end_turn", content: [{ type: "text", text: '{"activity_date":"2026-01-02","hours":"3","provider":"X","title":"Y","holder_name":null}' }] }) };
  };
  const r = await LLM.extractWithLlm("CERT TEXT 3 CPE", { fetchImpl: fakeFetch });
  assert.equal(seen.url, "https://api.anthropic.com/v1/messages");
  assert.equal(seen.init.headers["x-api-key"], "sk-test-123");
  const body = JSON.parse(seen.init.body);
  assert.equal(body.model, "claude-opus-5-5");
  assert.equal(body.messages.length, 1);
  assert.ok(body.messages[0].content.includes("CERT TEXT 3 CPE"));
  assert.equal(r.hours, 3);
  assert.equal(r.activity_date, "2026-01-02");
  LLM.forget();
  assert.deepEqual(LLM.status(), { on: false });
  await assert.rejects(LLM.extractWithLlm("x", { fetchImpl: fakeFetch }), /no API key/);
});

test("OpenAI-compatible request shape", async () => {
  LLM.configure({ provider: "openai", key: "k", model: "some-model", baseUrl: "http://localhost:11434/v1/" });
  let seen;
  const r = await LLM.extractWithLlm("T", { fetchImpl: async (url, init) => {
    seen = { url, init };
    return { ok: true, json: async () => ({ choices: [{ message: { content: "Sure: {\"hours\": 1.5, \"activity_date\": \"bad\"}" } }] }) };
  } });
  assert.equal(seen.url, "http://localhost:11434/v1/chat/completions");
  assert.equal(seen.init.headers.authorization, "Bearer k");
  assert.equal(r.hours, 1.5);
  assert.equal(r.activity_date, null, "a malformed date is dropped, not passed on");
  LLM.forget();
});

test("the key is never written by the module: no storage APIs in llm.js", () => {
  const src = readFileSync(new URL("../src/llm.js", import.meta.url), "utf8").replace(/^\s*\/\/.*$/gm, "");
  for (const api of ["localStorage", "sessionStorage", "indexedDB", "document.cookie", "caches"]) assert.ok(!src.includes(api), api);
});
