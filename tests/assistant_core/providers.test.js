const test = require("node:test");
const assert = require("node:assert/strict");
const Core = require("../../ahamargin/vendor/heehawpdf/core.js");

const opts = { model: "", sessionId: "abc", resume: false };

test("every provider builds an invocation", () => {
  for (const p of Core.PROVIDERS) {
    const inv = Core.invocation(p, { ...opts, customCommand: "cat", baseURL: "http://x/v1" });
    if (p.id === "custom") assert.deepEqual(inv.argv, ["@@SCRIPT@@", "cat"]);
    assert.ok(Array.isArray(inv.argv) && inv.argv.length, p.id);
    assert.ok(["claude", "text", "opencode", "sse"].includes(inv.format), p.id);
  }
});

test("claude keeps a session and has no tools", () => {
  const first = Core.invocation(Core.provider("claude"), { ...opts, model: "haiku" });
  assert.deepEqual(first.argv.slice(-4), ["--model", "haiku", "--session-id", "abc"]);
  assert.equal(first.argv[first.argv.indexOf("--tools") + 1], "");
  const next = Core.invocation(Core.provider("claude"), { ...opts, resume: true });
  assert.deepEqual(next.argv.slice(-2), ["--resume", "abc"]);
});

test("codex runs read-only and reports its last message", () => {
  const inv = Core.invocation(Core.provider("codex"), opts);
  assert.equal(inv.argv[inv.argv.indexOf("--sandbox") + 1], "read-only");
  assert.equal(inv.lastMessage, "last.txt");
  assert.equal(inv.stdin, true);
});

test("prompt-as-argument tools use the placeholder", () => {
  for (const id of ["opencode", "deepseek", "kimi"]) {
    const inv = Core.invocation(Core.provider(id), opts);
    assert.ok(inv.argv.includes("@@PROMPT@@"), id);
    assert.equal(inv.stdin, false, id);
  }
});

test("API providers post to /chat/completions with the key header", () => {
  const inv = Core.invocation(Core.provider("deepseek-api"), opts);
  assert.ok(inv.argv.includes("https://api.deepseek.com/chat/completions"));
  assert.ok(inv.argv.includes("@auth-header.txt"));
  const local = Core.invocation(Core.provider("ollama"), { ...opts, baseURL: "http://localhost:11434/v1/" });
  assert.ok(local.argv.includes("http://localhost:11434/v1/chat/completions"));
  assert.ok(!local.argv.includes("@auth-header.txt"));
});

test("statelessPrompt replays recent history", () => {
  const prompt = Core.statelessPrompt("SYS", [{ role: "user", content: "hi" }, { role: "assistant", content: "hello" }], "NOW");
  assert.equal(prompt, "SYS\n\nCONVERSATION SO FAR:\nUSER: hi\n\nASSISTANT: hello\n\nCURRENT MESSAGE:\nNOW");
});

test("chatRequest builds an OpenAI-style body", () => {
  const body = Core.chatRequest("m", "SYS", [{ role: "user", content: "a" }], "b");
  assert.deepEqual(body, {
    model: "m",
    stream: true,
    messages: [
      { role: "system", content: "SYS" },
      { role: "user", content: "a" },
      { role: "user", content: "b" },
    ],
  });
});

test("OutputState parses server-sent events", () => {
  const st = new Core.OutputState("sse");
  st.pushLine('data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}');
  assert.equal(st.thinking, true);
  st.pushLine('data: {"choices":[{"delta":{"content":"Hel"}}]}');
  st.pushLine("");
  st.pushLine('data: {"choices":[{"delta":{"content":"lo"}}]}');
  st.pushLine("data: [DONE]");
  st.pushLine("@@HTTP_STATUS 200");
  st.finish(0, "", "", "");
  assert.equal(st.text, "Hello");
  assert.equal(st.error, null);
});

test("OutputState reports API errors", () => {
  const st = new Core.OutputState("sse");
  st.pushLine('{"error":{"message":"Authentication Fails"}}');
  st.pushLine("@@HTTP_STATUS 401");
  st.finish(0, "", "", "");
  assert.match(st.error, /HTTP 401: Authentication Fails[\s\S]*API key/);
  const down = new Core.OutputState("sse");
  down.pushLine("@@HTTP_STATUS 000");
  down.finish(7, "curl: (7) Failed to connect", "", "");
  assert.match(down.error, /Failed to connect/);
});

test("OutputState parses opencode JSON events", () => {
  const st = new Core.OutputState("opencode");
  st.pushLine('{"type":"step_start","part":{"type":"step-start"}}');
  st.pushLine('{"type":"text","part":{"type":"text","text":"PONG"}}');
  st.finish(0, "", "", "");
  assert.equal(st.text, "PONG");
});

test("OutputState handles plain text, ANSI, and last-message files", () => {
  const st = new Core.OutputState("text");
  st.pushLine("\u001b[32mHello\u001b[0m");
  st.pushLine("world");
  st.finish(0, "", "", "");
  assert.equal(st.text, "Hello\nworld");
  const codex = new Core.OutputState("text");
  codex.pushLine("progress noise");
  codex.finish(0, "", "Final answer\n", "");
  assert.equal(codex.text, "Final answer");
  const failed = new Core.OutputState("text");
  failed.finish(1, "boom\n", "", "Run `x login`.");
  assert.equal(failed.error, "boom\n\nRun `x login`.");
});

test("OutputState wraps Claude stream-json", () => {
  const st = new Core.OutputState("claude");
  st.pushLine('{"type":"system","subtype":"init","session_id":"s9"}');
  st.pushLine('{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}}');
  st.pushLine('{"type":"result","subtype":"success","result":"Hi","session_id":"s9"}');
  st.finish(0, "", "", "");
  assert.equal(st.text, "Hi");
  assert.equal(st.sessionId, "s9");
  assert.equal(st.error, null);
});

test("OutputState uses stdout as the error when stderr is empty", () => {
  const st = new Core.OutputState("text");
  st.pushLine("LLM not set");
  st.finish(1, "", "", "Use /login.");
  assert.equal(st.error, "LLM not set\n\nUse /login.");
  assert.equal(st.text, "");
});
