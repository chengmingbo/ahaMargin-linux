// Adapted for native Qt: file/key helpers live in Python; no browser APIs are used.
/*
 * heehawPDF AI assistant extensions: logic shared by the Claude and the
 * multi-provider panels and by the tests. Nothing in here touches the DOM,
 * heehawPDF, or the shell.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.AssistantCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const NOTE_TYPES = ["highlight", "underline", "strikeout", "note"];

  const SYSTEM_PROMPT = `You are an AI reading assistant built into ahaMargin-linux, a PDF reader with linked margin notes.
The user is reading a PDF. Messages include the document's extracted text with page markers like "=== Page 12 ===" (or say it is unchanged since an earlier message), the page the user is on, and the text they selected, if any. Extracted PDF text can contain broken lines, hyphenation and stray headers; read through that.

Answer in Markdown and keep answers focused. Cite the pages you rely on as [p. 12] (or [pp. 12-14]); the viewer turns these into links.

The PDF itself cannot be edited, but you can annotate it. When the user asks you to highlight, mark, annotate or comment on passages, or when notes would clearly help, add blocks like these after a short explanation. The viewer shows each one as a card with an Add button. Never wrap blocks in code fences.

<note type="highlight" page="12">
<quote>exact passage copied from the page text</quote>
<comment>optional note attached to the highlight</comment>
</note>
  type is highlight, underline or strikeout. <quote> must be copied word for word from that page's text, short (one sentence or less) and unique on the page. <comment> may be omitted.

<note type="note" page="12">
<quote>optional passage the note sits next to</quote>
<comment>the note's text</comment>
</note>
  A sticky note on the page, next to the quote if given.`;

  /** Removes one leading and one trailing newline, which the block syntax adds. */
  function trimBlock(text) {
    return text.replace(/^\r?\n/, "").replace(/\r?\n$/, "");
  }

  function attribute(attrs, name) {
    const match = new RegExp(`${name}\\s*=\\s*["']?([^"'\\s>]+)["']?`, "i").exec(attrs);
    return match ? match[1] : null;
  }

  function parseNote(attrs, body) {
    const type = (attribute(attrs, "type") || "note").toLowerCase().replace(/[-_ ]/g, "");
    const page = parseInt(attribute(attrs, "page") || "", 10);
    const quote = /<quote>([\s\S]*?)<\/quote>/.exec(body);
    const comment = /<(comment|text)>([\s\S]*?)<\/\1>/.exec(body);
    const note = {
      type: type === "strike" ? "strikeout" : type,
      page: Number.isFinite(page) && page > 0 ? page : null,
      quote: quote ? quote[1].trim() : "",
      comment: comment ? trimBlock(comment[2]).trim() : "",
    };
    if (!quote && !comment) note.comment = trimBlock(body).trim();
    if (!NOTE_TYPES.includes(note.type)) note.invalid = `Unknown note type “${note.type}”.`;
    else if (note.type !== "note" && !note.quote) note.invalid = `A ${note.type} needs a <quote>.`;
    else if (note.type === "note" && !note.comment) note.invalid = "The note has no text.";
    return note;
  }

  /**
   * Splits an assistant reply into text and note segments. While a reply is
   * still streaming, an unfinished trailing block becomes a `pending` segment.
   */
  function parseReply(reply, streaming) {
    const segments = [];
    const pattern = /<note\b([^>]*)>([\s\S]*?)<\/note>/g;
    let last = 0;
    let match;
    const pushText = (text) => {
      if (text.trim()) segments.push({ kind: "text", text });
    };
    while ((match = pattern.exec(reply))) {
      pushText(reply.slice(last, match.index));
      segments.push({ kind: "note", note: parseNote(match[1], match[2]) });
      last = pattern.lastIndex;
    }
    const tail = reply.slice(last);
    const open = tail.search(/<note\b/);
    if (open >= 0) {
      pushText(tail.slice(0, open));
      segments.push({ kind: streaming ? "pending" : "text", text: tail.slice(open) });
    } else if (streaming) {
      const partial = /<(n(o(t(e)?)?)?)?$/.exec(tail);
      pushText(partial ? tail.slice(0, partial.index) : tail);
    } else {
      pushText(tail);
    }
    return segments;
  }

  class StreamState {
    constructor() {
      this.text = "";
      this.thinking = false;
      this.activity = "";
      this.done = false;
      this.error = null;
      this.sessionId = null;
      this.cost = null;
      this.durationMs = null;
      this._sawDelta = false;
    }

    /** Feeds one parsed JSON event. Returns true if the display should update. */
    push(event) {
      if (!event || typeof event !== "object") return false;
      switch (event.type) {
        case "system":
          if (event.session_id) this.sessionId = event.session_id;
          return false;
        case "stream_event":
          return this._streamEvent(event.event || {});
        case "assistant": {
          // Complete message. Only used when partial deltas were not seen.
          const content = (event.message && event.message.content) || [];
          if (!this._sawDelta) {
            const text = content.filter((c) => c.type === "text").map((c) => c.text).join("");
            if (text) {
              this.text += text;
              return true;
            }
          }
          const tool = content.find((c) => c.type === "tool_use");
          if (tool) {
            this.activity = describeTool(tool);
            return true;
          }
          return false;
        }
        case "user":
          this.activity = "";
          return true;
        case "result":
          this.done = true;
          this.thinking = false;
          this.activity = "";
          if (event.session_id) this.sessionId = event.session_id;
          if (typeof event.total_cost_usd === "number") this.cost = event.total_cost_usd;
          if (typeof event.duration_ms === "number") this.durationMs = event.duration_ms;
          if (event.is_error || (event.subtype && event.subtype !== "success")) {
            this.error = event.result || (Array.isArray(event.errors) && event.errors.join("\n")) || event.subtype || "Claude reported an error.";
          } else if (!this.text.trim() && typeof event.result === "string") {
            this.text = event.result;
          }
          return true;
        default:
          return false;
      }
    }

    _streamEvent(e) {
      if (e.type === "content_block_start") {
        const block = e.content_block || {};
        if (block.type === "thinking") this.thinking = true;
        if (block.type === "text") this.thinking = false;
        if (block.type === "tool_use") this.activity = describeTool(block);
        // Separate the text of successive assistant turns (after tool use).
        if (block.type === "text" && this.text && !this.text.endsWith("\n")) this.text += "\n\n";
        return true;
      }
      if (e.type === "content_block_delta" && e.delta) {
        if (e.delta.type === "text_delta") {
          this._sawDelta = true;
          this.thinking = false;
          this.activity = "";
          this.text += e.delta.text;
          return true;
        }
        if (e.delta.type === "thinking_delta" && !this.thinking) {
          this.thinking = true;
          return true;
        }
      }
      return false;
    }
  }

  function describeTool(block) {
    const input = block.input || {};
    const target = input.file_path || input.path || input.pattern || "";
    const name = block.name || "tool";
    return target ? `${name}: ${String(target).split("/").pop()}` : `Using ${name}…`;
  }

  /**
   * Splits a chunk from `tail -n +K out.jsonl` into complete lines. Returns
   * the complete lines; a trailing partial line is left for the next poll.
   */
  function completeLines(chunk) {
    if (!chunk) return [];
    const lines = chunk.split("\n");
    lines.pop(); // either "" after a final newline, or a partial line
    return lines;
  }

  /** Builds the per-turn prompt from the editor context. */

  /**
   * Builds the per-turn prompt. pages: [{page, label, text}]; when the text
   * exceeds maxChars, pages around the current page are kept.
   */
  function buildPrompt(ctx, request) {
    const parts = [];
    const name = ctx.name || "Untitled.pdf";
    const meta = [`name="${escapeAttr(name)}"`, `pages="${ctx.pageCount || 0}"`];
    if (ctx.title) meta.push(`title="${escapeAttr(ctx.title)}"`);
    if (ctx.documentUnchanged) {
      parts.push(`<document ${meta.join(" ")} unchanged="true">The document text is unchanged since my earlier message.</document>`);
    } else {
      const { text, omitted } = selectPages(ctx.pages || [], ctx.currentPage || 1, ctx.maxChars || Infinity);
      if (omitted) meta.push(`omitted_pages="${omitted}"`);
      parts.push(`<document ${meta.join(" ")}>\n${text}\n</document>`);
    }
    if (ctx.currentPage) parts.push(`<current_page>${ctx.currentPage}</current_page>`);
    if (ctx.selection && ctx.selection.text && ctx.selection.text.trim()) {
      parts.push(`<selection page="${ctx.selection.page}">\n${ctx.selection.text}\n</selection>`);
    }
    parts.push(`<request>\n${request}\n</request>`);
    return parts.join("\n\n");
  }

  function pageBlock(p) {
    const label = p.label && p.label !== String(p.page) ? ` (label ${p.label})` : "";
    return `=== Page ${p.page}${label} ===\n${(p.text || "").trim()}`;
  }

  /** Keeps whole pages, growing outwards from the current page, within maxChars. */
  function selectPages(pages, current, maxChars) {
    const blocks = pages.map(pageBlock);
    const total = blocks.reduce((n, b) => n + b.length + 2, 0);
    if (total <= maxChars) return { text: blocks.join("\n\n"), omitted: "" };
    const start = Math.max(0, pages.findIndex((p) => p.page === current));
    const keep = new Set([start]);
    let used = blocks[start].length;
    for (let step = 1; step < pages.length; step++) {
      let grew = false;
      for (const i of [start + step, start - step]) {
        if (i < 0 || i >= pages.length) continue;
        if (used + blocks[i].length + 2 > maxChars) continue;
        keep.add(i);
        used += blocks[i].length + 2;
        grew = true;
      }
      if (!grew && start + step >= pages.length && start - step < 0) break;
    }
    const kept = [...keep].sort((a, b) => a - b);
    const omittedPages = pages.filter((_, i) => !keep.has(i)).map((p) => p.page);
    return { text: kept.map((i) => blocks[i]).join("\n\n"), omitted: ranges(omittedPages) };
  }

  function ranges(numbers) {
    const out = [];
    for (let i = 0; i < numbers.length; i++) {
      let j = i;
      while (j + 1 < numbers.length && numbers[j + 1] === numbers[j] + 1) j++;
      out.push(i === j ? `${numbers[i]}` : `${numbers[i]}-${numbers[j]}`);
      i = j;
    }
    return out.join(",");
  }

  function escapeAttr(text) {
    return String(text).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  /** POSIX shell single-quoting. */
  function shQuote(value) {
    return "'" + String(value).replace(/'/g, "'\\''") + "'";
  }

  /** Cheap, stable string hash used to notice document changes. */
  function hash(text) {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16) + ":" + text.length;
  }

  function uuid() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  /** UTF-8 safe base64 for moving text through shell commands. */
  function toBase64(text) {
    if (typeof Buffer !== "undefined") return Buffer.from(text, "utf8").toString("base64");
    const bytes = new TextEncoder().encode(text);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
  }

  /**
   * Minimal, HTML-safe Markdown renderer for chat bubbles: fenced code,
   * headings, lists, quotes, paragraphs, and inline code/bold/italic/links.
   */
  function renderMarkdown(source) {
    const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const inline = (s) => {
      const codes = [];
      s = s.replace(/`([^`\n]+)`/g, (_, code) => {
        codes.push(code);
        return `\u0000${codes.length - 1}\u0000`;
      });
      s = esc(s)
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
        .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>');
      return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
    };
    const lines = String(source).replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = /^\s*(```|~~~)(.*)$/.exec(line);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
        continue;
      }
      const heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        const level = Math.min(heading[1].length + 2, 6);
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        i++;
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
        const ordered = /^\s*\d/.test(line);
        const items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          items.push(`<li>${inline(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ""))}</li>`);
          i++;
        }
        out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
        continue;
      }
      if (/^>\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ""));
        out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
        continue;
      }
      if (!line.trim()) {
        i++;
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|~~~|#{1,6}\s|>|([-*+]|\d+[.)])\s)/.test(lines[i])) {
        para.push(lines[i++]);
      }
      if (!para.length) para.push(lines[i++]);
      out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    }
    return out.join("");
  }


  /** Turns [p. 12] / [pp. 12-14] / [page 3] citations into links the panel handles. */
  function linkCitations(html) {
    return html.replace(/\[(?:pp?\.|pages?)\s*(\d+)(?:\s*[-–]\s*(\d+))?\]/gi, (all, from) =>
      `<a href="#page-${from}" class="cite" data-page="${from}">${all}</a>`);
  }

  // ---------------------------------------------------------------------------
  // Providers

  /**
   * AI tools the panel can drive. CLI tools run in their non-interactive
   * mode; API providers are OpenAI-compatible /chat/completions endpoints
   * streamed with curl.
   */
  const PROVIDERS = [
    { id: "claude", title: "Claude", kind: "cli", command: "claude", format: "claude", sessions: true,
      models: ["haiku", "sonnet", "opus"], defaultModel: "sonnet",
      setup: "Run `claude auth login` in a terminal.", note: "Claude Code CLI · no file access" },
    { id: "codex", title: "Codex", kind: "cli", command: "codex", format: "text", models: [],
      setup: "Run `codex login` in a terminal.", note: "Codex CLI · read-only sandbox" },
    { id: "opencode", title: "OpenCode", kind: "cli", command: "opencode", format: "opencode", models: [],
      setup: "Run `opencode auth login` in a terminal.", note: "OpenCode CLI · model as provider/model" },
    { id: "deepseek", title: "DeepSeek", kind: "cli", command: "deepseek", format: "text", models: [],
      setup: "Run `deepseek login` in a terminal.", note: "DeepSeek TUI (deepseek exec)" },
    { id: "kimi", title: "Kimi", kind: "cli", command: "kimi", format: "text", models: [],
      setup: "Run `kimi` once in a terminal and use /login to choose a model.", note: "Kimi CLI (print mode)" },
    { id: "qwen", title: "Qwen Code", kind: "cli", command: "qwen", format: "text", models: [],
      setup: "Run `qwen` once in a terminal and use /auth to choose a provider.", note: "Qwen Code CLI" },
    { id: "gemini", title: "Gemini", kind: "cli", command: "gemini", format: "text", models: ["gemini-2.5-flash", "gemini-2.5-pro"],
      setup: "Run `gemini` once in a terminal to sign in.", note: "Gemini CLI" },
    { id: "deepseek-api", title: "DeepSeek API", kind: "api", baseURL: "https://api.deepseek.com",
      models: ["deepseek-chat", "deepseek-reasoner"], keyVar: "DEEPSEEK_API_KEY", note: "platform.deepseek.com" },
    { id: "kimi-api", title: "Kimi API", kind: "api", baseURL: "https://api.moonshot.cn/v1",
      models: ["kimi-k2-turbo-preview", "kimi-k2-0905-preview"], keyVar: "MOONSHOT_API_KEY", note: "platform.moonshot.cn" },
    { id: "qwen-api", title: "Qwen API", kind: "api", baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      models: ["qwen-plus", "qwen-max", "qwen-turbo"], keyVar: "DASHSCOPE_API_KEY", note: "Alibaba Cloud DashScope" },
    { id: "glm-api", title: "GLM API", kind: "api", baseURL: "https://open.bigmodel.cn/api/paas/v4",
      models: ["glm-4.6", "glm-4.5-air"], keyVar: "ZHIPUAI_API_KEY", note: "Zhipu open.bigmodel.cn" },
    { id: "minimax-api", title: "MiniMax API", kind: "api", baseURL: "https://api.minimaxi.com/v1",
      models: ["MiniMax-M2.7-highspeed", "MiniMax-M2.7"], keyVar: "MINIMAX_API_KEY", note: "api.minimaxi.com (global: api.minimax.io)" },
    { id: "openai-api", title: "OpenAI API", kind: "api", baseURL: "https://api.openai.com/v1",
      models: ["gpt-5-mini", "gpt-5"], keyVar: "OPENAI_API_KEY", note: "platform.openai.com" },
    { id: "openrouter", title: "OpenRouter", kind: "api", baseURL: "https://openrouter.ai/api/v1",
      models: ["deepseek/deepseek-chat", "qwen/qwen3-coder", "moonshotai/kimi-k2"], keyVar: "OPENROUTER_API_KEY", note: "openrouter.ai" },
    { id: "ollama", title: "Ollama (local)", kind: "api", baseURL: "http://localhost:11434/v1",
      models: ["qwen2.5", "llama3.1", "deepseek-r1"], keyVar: null, note: "Local models, no key" },
    { id: "custom-api", title: "Custom API", kind: "api", baseURL: "",
      models: [], keyVar: "AI_API_KEY", note: "Any OpenAI-compatible endpoint" },
    { id: "custom", title: "Custom command", kind: "cli", command: "", format: "text", models: [],
      setup: "Set the command in Settings.", note: "Prompt on stdin, answer on stdout" },
  ];

  function provider(id) {
    return PROVIDERS.find((p) => p.id === id) || PROVIDERS[0];
  }

  const STDIN_INSTRUCTION = "Follow the instructions provided on standard input.";

  /**
   * Builds how the runner launches one turn. Pure, so it can be tested:
   *   argv    command and arguments; "@@PROMPT@@" is replaced by prompt.txt
   *           and "@@SYSTEM@@" by system.txt inside the runner
   *   stdin   feed prompt.txt on standard input
   *   format  how to parse the output
   */
  function invocation(p, opts) {
    const model = (opts.model || "").trim();
    switch (p.id) {
      case "claude": {
        const argv = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
          "--setting-sources", "project", "--strict-mcp-config", "--tools", "", "--system-prompt", "@@SYSTEM@@"];
        if (model) argv.push("--model", model);
        argv.push(opts.resume ? "--resume" : "--session-id", opts.sessionId);
        return { argv, stdin: true, format: "claude" };
      }
      case "codex": {
        const argv = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
          "--output-last-message", "last.txt"];
        if (model) argv.push("--model", model);
        argv.push("-");
        return { argv, stdin: true, format: "text", lastMessage: "last.txt" };
      }
      case "opencode": {
        const argv = ["opencode", "run", "--format", "json"];
        if (model) argv.push("--model", model);
        argv.push("@@PROMPT@@");
        return { argv, stdin: false, format: "opencode" };
      }
      case "deepseek":
        return { argv: ["deepseek", "exec", "@@PROMPT@@"], stdin: false, format: "text" };
      case "kimi": {
        const argv = ["kimi", "--quiet"];
        if (model) argv.push("--model", model);
        argv.push("--prompt", "@@PROMPT@@");
        return { argv, stdin: false, format: "text" };
      }
      case "qwen":
      case "gemini": {
        const argv = [p.command, "--output-format", "text"];
        if (model) argv.push("--model", model);
        argv.push("--prompt", STDIN_INSTRUCTION);
        return { argv, stdin: true, format: "text" };
      }
      case "custom":
        // Run as shell source in the login shell, so aliases work (as in mooTerm).
        return { argv: ["@@SCRIPT@@", opts.customCommand || ""], stdin: true, format: "text" };
      default: {
        // OpenAI-compatible chat completions API, streamed as server-sent events.
        const base = (opts.baseURL || p.baseURL || "").trim().replace(/\/+$/, "");
        const url = /\/chat\/completions$/.test(base) ? base : `${base}/chat/completions`;
        const argv = ["curl", "-sS", "-N", "-X", "POST", url, "-H", "Content-Type: application/json"];
        if (p.keyVar) argv.push("-H", "@auth-header.txt");
        argv.push("--data-binary", "@request.json", "-w", "\n@@HTTP_STATUS %{http_code}\n");
        return { argv, stdin: false, format: "sse" };
      }
    }
  }

  /**
   * Stateless tools get the recent conversation replayed in each prompt, the
   * way NemoMac's assistant does.
   */
  function statelessPrompt(system, history, current) {
    const sections = [system];
    const recent = history.slice(-12);
    if (recent.length) {
      const transcript = recent
        .map((m) => {
          const content = m.content.length > 4000 ? m.content.slice(0, 4000) + " […]" : m.content;
          return `${m.role === "user" ? "USER" : "ASSISTANT"}: ${content}`;
        })
        .join("\n\n");
      sections.push(`CONVERSATION SO FAR:\n${transcript}`);
    }
    sections.push(`CURRENT MESSAGE:\n${current}`);
    return sections.join("\n\n");
  }

  /** Body for an OpenAI-compatible /chat/completions request. */
  function chatRequest(model, system, history, current) {
    const messages = [{ role: "system", content: system }];
    for (const m of history.slice(-20)) messages.push({ role: m.role, content: m.content });
    messages.push({ role: "user", content: current });
    return { model, stream: true, messages };
  }

  const ANSI = /\u001b\[[0-9;?]*[ -\/]*[@-~]|\u001b\][^\u0007]*(\u0007|\u001b\\)/g;

  /**
   * Parses a tool's output, one complete line at a time, into the state the
   * panel renders ({text, thinking, activity, error, done, …}).
   */
  class OutputState {
    constructor(format) {
      this.format = format;
      this.stream = format === "claude" ? new StreamState() : null;
      this.text = "";
      this.thinking = false;
      this.activity = "";
      this.done = false;
      this.error = null;
      this.sessionId = null;
      this.cost = null;
      this.durationMs = null;
      this.httpStatus = null;
      this._body = [];
    }

    pushLine(line) {
      switch (this.format) {
        case "claude": {
          let event;
          try {
            event = JSON.parse(line);
          } catch (e) {
            return false;
          }
          const changed = this.stream.push(event);
          for (const key of ["text", "thinking", "activity", "done", "error", "sessionId", "cost", "durationMs"]) {
            this[key] = this.stream[key];
          }
          return changed;
        }
        case "sse":
          return this._sse(line);
        case "opencode":
          return this._opencode(line);
        default:
          this.text += line.replace(ANSI, "").replace(/\r/g, "") + "\n";
          return true;
      }
    }

    _sse(line) {
      const status = /^@@HTTP_STATUS (\d+)/.exec(line);
      if (status) {
        this.httpStatus = +status[1];
        if (this.httpStatus >= 400) {
          this.error = apiError(this.httpStatus, this._body.join("\n"));
        }
        return true;
      }
      if (!line.startsWith("data:")) {
        if (line.trim() && !line.startsWith(":") && !/^(event|id|retry):/.test(line)) this._body.push(line);
        return false;
      }
      const data = line.slice(5).trim();
      if (data === "[DONE]") return false;
      let event;
      try {
        event = JSON.parse(data);
      } catch (e) {
        return false;
      }
      if (event.error) {
        this.error = event.error.message || JSON.stringify(event.error);
        return true;
      }
      const delta = (event.choices && event.choices[0] && event.choices[0].delta) || {};
      if (delta.content) {
        this.text += delta.content;
        this.thinking = false;
        return true;
      }
      if (delta.reasoning_content || delta.reasoning) {
        if (!this.thinking && !this.text) {
          this.thinking = true;
          return true;
        }
      }
      return false;
    }

    _opencode(line) {
      let event;
      try {
        event = JSON.parse(line);
      } catch (e) {
        return false;
      }
      const part = event.part || {};
      if (event.type === "text" && part.text) {
        this.text += (this.text ? "\n\n" : "") + part.text;
        this.activity = "";
        return true;
      }
      if (event.type === "tool_use" || part.type === "tool") {
        this.activity = `Using ${part.tool || "a tool"}…`;
        return true;
      }
      if (event.type === "error") {
        const err = event.error || {};
        this.error = (err.data && err.data.message) || err.message || JSON.stringify(err);
        return true;
      }
      return false;
    }

    /** Called once the process has exited. */
    finish(exitCode, stderr, lastMessage, setupHint) {
      this.done = true;
      this.thinking = false;
      this.activity = "";
      if (this.format === "text") {
        this.text = (lastMessage && lastMessage.trim() ? lastMessage : this.text).trim();
      }
      if (this.error) return;
      let detail = lastLines(stderr.replace(ANSI, ""));
      if (exitCode !== 0 && !detail && this.text.trim()) {
        // Some tools (Kimi, for one) print their errors on standard output.
        detail = lastLines(this.text);
        this.text = "";
      }
      if (exitCode === 127) {
        this.error = detail || "The tool was not found on your PATH.";
      } else if (exitCode !== 0) {
        this.error = [detail || `The tool exited with status ${exitCode}.`, setupHint].filter(Boolean).join("\n\n");
      } else if (this.format === "sse" && !this.httpStatus) {
        this.error = detail || "Couldn't connect to the API. Check the base URL and proxy.";
      } else if (!this.text.trim()) {
        this.error = [detail || "The tool returned no answer.", setupHint].filter(Boolean).join("\n\n");
      }
    }
  }

  function apiError(status, body) {
    let message = body.trim();
    try {
      const json = JSON.parse(body);
      message = (json.error && (json.error.message || json.error)) || json.message || message;
      if (typeof message !== "string") message = JSON.stringify(message);
    } catch (e) {
      /* plain-text body */
    }
    const hint = status === 401 || status === 403 ? "\n\nCheck the API key in Settings." : "";
    return `HTTP ${status}${message ? `: ${message}` : ""}${hint}`;
  }

  function lastLines(text) {
    return text
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l && !/^at /.test(l))
      .slice(-6)
      .join("\n");
  }

  return {
    SYSTEM_PROMPT,
    NOTE_TYPES,
    PROVIDERS,
    provider,
    invocation,
    statelessPrompt,
    chatRequest,
    OutputState,
    parseReply,
    StreamState,
    completeLines,
    buildPrompt,
    selectPages,
    shQuote,
    hash,
    uuid,
    toBase64,
    renderMarkdown,
    linkCitations,
  };
});
