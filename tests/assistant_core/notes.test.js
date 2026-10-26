const test = require("node:test");
const assert = require("node:assert/strict");
const Core = require("../../ahamargin/vendor/heehawpdf/core.js");

test("parseReply extracts note blocks between prose", () => {
  const reply = `RuBisCO fixes carbon [p. 1].

<note type="highlight" page="1">
<quote>The key enzyme of carbon fixation is RuBisCO</quote>
<comment>Central enzyme</comment>
</note>

<note type="note" page="2">
<comment>Compare with C4 plants.</comment>
</note>`;
  const segments = Core.parseReply(reply, false);
  assert.deepEqual(segments.map((s) => s.kind), ["text", "note", "note"]);
  assert.deepEqual(segments[1].note, { type: "highlight", page: 1, quote: "The key enzyme of carbon fixation is RuBisCO", comment: "Central enzyme" });
  assert.deepEqual(segments[2].note, { type: "note", page: 2, quote: "", comment: "Compare with C4 plants." });
});

test("parseReply validates notes", () => {
  const [noQuote] = Core.parseReply('<note type="underline" page="1"><comment>x</comment></note>', false);
  assert.match(noQuote.note.invalid, /needs a <quote>/);
  const [empty] = Core.parseReply('<note type="note"><quote>q</quote></note>', false);
  assert.match(empty.note.invalid, /no text/);
  const [unknown] = Core.parseReply('<note type="circle"><quote>q</quote></note>', false);
  assert.match(unknown.note.invalid, /Unknown/);
  const [strike] = Core.parseReply("<note type='strike-out' page=3><quote>q</quote></note>", false);
  assert.equal(strike.note.type, "strikeout");
  assert.equal(strike.note.page, 3);
  const [bare] = Core.parseReply('<note type="note">Just text</note>', false);
  assert.equal(bare.note.comment, "Just text");
});

test("parseReply hides unfinished blocks while streaming", () => {
  assert.deepEqual(Core.parseReply('Done.\n<note type="highlight"><quote>abc', true).map((s) => s.kind), ["text", "pending"]);
  assert.equal(Core.parseReply("Here <no", true)[0].text, "Here ");
});

test("buildPrompt includes page markers, current page and selection", () => {
  const pages = [{ page: 1, label: "i", text: "Front matter" }, { page: 2, label: "2", text: "Body text" }];
  const prompt = Core.buildPrompt({ name: "a.pdf", pageCount: 2, currentPage: 2, pages, selection: { text: "Body", page: 2 } }, "Explain");
  assert.match(prompt, /<document name="a.pdf" pages="2">\n=== Page 1 \(label i\) ===\nFront matter\n\n=== Page 2 ===\nBody text\n<\/document>/);
  assert.match(prompt, /<current_page>2<\/current_page>/);
  assert.match(prompt, /<selection page="2">\nBody\n<\/selection>/);
  assert.match(prompt, /<request>\nExplain\n<\/request>$/);
  assert.match(Core.buildPrompt({ name: "a.pdf", documentUnchanged: true }, "x"), /unchanged="true"/);
});

test("selectPages keeps pages around the current page within the budget", () => {
  const pages = Array.from({ length: 10 }, (_, i) => ({ page: i + 1, text: "x".repeat(100) }));
  const { text, omitted } = Core.selectPages(pages, 5, 400);
  const kept = [...text.matchAll(/=== Page (\d+)/g)].map((m) => +m[1]);
  assert.ok(kept.includes(5));
  assert.ok(kept.length >= 2 && kept.length <= 4);
  assert.deepEqual(kept, [...kept].sort((a, b) => a - b));
  assert.ok(omitted.includes("1"));
  assert.equal(Core.selectPages(pages, 1, Infinity).omitted, "");
});

test("linkCitations turns page citations into links", () => {
  const html = Core.linkCitations(Core.renderMarkdown("See [p. 12] and [pp. 3-4], not [x]."));
  assert.match(html, /<a href="#page-12" class="cite" data-page="12">\[p\. 12\]<\/a>/);
  assert.match(html, /data-page="3">\[pp\. 3-4\]<\/a>/);
  assert.ok(html.includes("[x]"));
});

test("renderMarkdown escapes HTML from the model", () => {
  assert.ok(!Core.renderMarkdown("<img src=x onerror=alert(1)>").includes("<img"));
});
