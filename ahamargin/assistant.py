"""Native host for heehawPDF's assistant protocol and process runner."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QStandardPaths, QTimer, Signal
from PySide6.QtQml import QJSEngine

from . import APP_NAME
from .core.annotations import AnnKind, Annotation
from .core.geometry import Rect

VENDOR = Path(__file__).parent / "vendor/heehawpdf"
KEY_SERVICE = "ahamargin-ai-assistant"


class AssistantCore:
    """Run the shared provider, prompt, Markdown, and streaming logic in Qt."""

    def __init__(self):
        self.engine = QJSEngine()
        self.evaluate((VENDOR / "core.js").read_text())

    def evaluate(self, source):
        result = self.engine.evaluate(source)
        if result.isError():
            raise RuntimeError(f"Assistant: {result.toString()} (line {result.property('lineNumber').toInt()})")
        return result

    def call(self, function, *args):
        arguments = ",".join(json.dumps(arg, ensure_ascii=True) for arg in args)
        result = self.evaluate(f"JSON.stringify(AssistantCore.{function}({arguments}))")
        return json.loads(result.toString())

    @property
    def providers(self):
        return json.loads(self.evaluate("JSON.stringify(AssistantCore.PROVIDERS)").toString())

    @property
    def system_prompt(self):
        return self.evaluate("AssistantCore.SYSTEM_PROMPT").toString().replace(
            "A sticky note on the page, next to the quote if given.",
            "A linked margin note beside the PDF, anchored to the quote if given.",
        )

    def start_output(self, format):
        self.evaluate(f"var output = new AssistantCore.OutputState({json.dumps(format)});")

    def feed(self, lines):
        self.evaluate(f"{json.dumps(lines, ensure_ascii=True)}.forEach(function(line) {{ output.pushLine(line); }});")

    def finish_output(self, code, stderr, last, hint):
        args = ",".join(json.dumps(arg, ensure_ascii=True) for arg in (code, stderr, last, hint))
        self.evaluate(f"output.finish({args});")

    def output(self):
        return json.loads(self.evaluate("JSON.stringify({text:output.text,error:output.error,"
            "thinking:output.thinking,activity:output.activity,sessionId:output.sessionId})").toString())

    def render(self, text):
        return self.call("linkCitations", self.call("renderMarkdown", text))


class AssistantConfig:
    def __init__(self, core, settings):
        self.core, self.settings = core, settings
        try:
            self.values = json.loads(settings.value("ai/config", "{}"))
            if not isinstance(self.values, dict):
                self.values = {}
        except (ValueError, TypeError):
            self.values = {}
        self.values.setdefault("provider", "claude")
        self.values.setdefault("providers", {})
        self.values.setdefault("customApis", [])

    def save(self):
        self.settings.setValue("ai/config", json.dumps(self.values))
        self.settings.sync()

    def providers(self):
        custom = [dict(id=api["id"], title=api["name"], kind="api", custom=True,
                       baseURL=api["baseURL"], models=[api.get("model", "")],
                       defaultModel=api.get("model", ""),
                       keyVar=None if api.get("noKey") else "AI_API_KEY")
                  for api in self.values["customApis"]]
        return self.core.providers + custom

    def provider(self, id=None):
        id = id or self.values["provider"]
        return next((p for p in self.providers() if p["id"] == id), self.core.providers[0])

    def options(self, provider):
        stored = self.values["providers"].get(provider["id"], {})
        models = provider.get("models", [])
        return dict(model=stored.get("model", provider.get("defaultModel", models[0] if provider["kind"] == "api" and models else "")),
                    baseURL=stored.get("baseURL", provider.get("baseURL", "")),
                    bin=stored.get("bin", ""), command=stored.get("command", ""),
                    noKey=stored.get("noKey", not provider.get("keyVar")))


class KeyStore:
    """Use the desktop keyring, with the same private-file fallback as heehawPDF."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)) / APP_NAME
        self.path = self.directory / "keys.json"

    def _read(self):
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def _write(self, values):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".keys-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(values, stream, separators=(",", ":"))
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def set(self, account, key):
        if "\n" in key or "\r" in key:
            raise ValueError("An API key must be a single line.")
        tool = shutil.which("secret-tool")
        if tool:
            try:
                result = subprocess.run([tool, "store", "--label=ahaMargin-linux API key",
                    "service", KEY_SERVICE, "account", account], input=key, text=True,
                    capture_output=True, timeout=5)
                if result.returncode == 0:
                    values = self._read()
                    if account in values:
                        del values[account]
                        self._write(values)
                    return "API key saved in the desktop keyring."
            except (OSError, subprocess.TimeoutExpired):
                pass
        values = self._read()
        values[account] = key
        self._write(values)
        return "API key saved in an owner-only file (desktop keyring unavailable)."

    def remove(self, account):
        tool = shutil.which("secret-tool")
        if tool:
            try:
                subprocess.run([tool, "clear", "service", KEY_SERVICE, "account", account],
                               capture_output=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        values = self._read()
        if account in values:
            del values[account]
            self._write(values)


def validate_options(provider, options):
    if provider["kind"] == "api":
        url = urlsplit(options.get("baseURL", "").strip())
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise ValueError("Enter an http:// or https:// API base URL without credentials.")
        if not options.get("model", "").strip():
            raise ValueError("Enter the API model name in Settings.")
    if provider["id"] == "custom" and not options.get("command", "").strip():
        raise ValueError("Enter the custom command in Settings first.")


# Pictures pasted into the message box.
MAX_IMAGES = 4
MAX_IMAGE_EDGE = 1568
MAX_THUMB_EDGE = 240
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def fit_size(width, height, max_edge):
    """Scale width and height so the long edge is at most max_edge; never enlarges."""
    if width <= 0 or height <= 0:
        return 0, 0
    scale = min(1.0, max_edge / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


class AssistantImage:
    """A picture pasted into the assistant, sent with the next question."""

    def __init__(self, data, media_type="image/jpeg", thumb=None):
        self.data = data
        self.media_type = media_type
        self.thumb = thumb

    def data_url(self):
        return "data:" + self.media_type + ";base64," + base64.b64encode(self.data).decode("ascii")

    def content_part(self):
        """One image_url content part, the shape OpenAI-compatible APIs expect."""
        return {"type": "image_url", "image_url": {"url": self.data_url()}}


def image_content(text, images):
    """The content of a user message that carries pictures: the text first, then one image_url block per picture."""
    parts = [{"type": "text", "text": text or ""}]
    for image in images or []:
        parts.append(image.content_part())
    return parts


def figure_name(index):
    """The name a picture gets in the job folder."""
    return f"figure-{index + 1}.jpg"


def figures_prompt(images):
    """The prompt section for a turn whose pictures travel as files the agent can read."""
    if not images:
        return ""
    lines = []
    for index in range(len(images)):
        lines.append(f'<figure path="./{figure_name(index)}">'
                     "The user attached a picture. Read this file to see it.</figure>")
    return "\n".join(lines)


def allow_read_tool(invocation):
    """Give the agent the Read tool for this turn, so it can open a pasted picture."""
    argv = list(invocation.get("argv", []))
    if "--tools" in argv:
        argv[argv.index("--tools") + 1] = "Read"
    return dict(invocation, argv=argv)


def validate_images(images):
    """Reject pictures we cannot send, or too many of them."""
    if not images:
        return
    if len(images) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} pictures per message.")
    for image in images:
        if image.media_type not in ("image/jpeg", "image/png"):
            raise ValueError("A pasted picture must be a JPEG or a PNG.")
        if not image.data:
            raise ValueError("A pasted picture is empty.")
        if len(image.data) > MAX_IMAGE_BYTES:
            raise ValueError("A pasted picture is too large to send.")


class Agent(QObject):
    updated = Signal(object)
    finished = Signal(object)
    runningChanged = Signal(bool)

    def __init__(self, core, config, keys, parent=None):
        super().__init__(parent)
        self.core, self.config, self.keys = core, config, keys
        self.process = QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        self.job = None
        self.running = False
        self.reset_session()

    def reset_session(self):
        self.session_id = str(uuid.uuid4())
        self.session_provider = None

    def run(self, prompt, history, provider=None, options=None, testing=False, images=None):
        if self.running:
            raise ValueError("The assistant is already answering.")
        validate_images(images)
        provider = dict(provider or self.config.provider())
        options = options or self.config.options(provider)
        validate_options(provider, options)
        if options.get("noKey"):
            provider["keyVar"] = None
        self.provider, self.testing = provider, testing
        invocation = self.core.call("invocation", provider, dict(options, customCommand=options.get("command", ""),
            sessionId=str(uuid.uuid4()) if testing else self.session_id,
            resume=not testing and self.session_provider == provider["id"]))
        if images and provider["id"] == "claude":
            # Claude may read the picture from the job folder, for this turn only.
            invocation = allow_read_tool(invocation)
        # A private root prevents another local user from reading PDF context.
        root = Path(tempfile.gettempdir()) / f"ahamargin-ai-{os.getuid()}"
        root.mkdir(mode=0o700, exist_ok=True)
        if root.is_symlink() or root.stat().st_uid != os.getuid():
            raise ValueError("The assistant temporary folder is not owned by your user.")
        root.chmod(0o700)
        self.job = Path(tempfile.mkdtemp(prefix="turn-", dir=root))
        self.invocation = invocation
        system = self.core.system_prompt
        instructions = self.config.values.get("instructions", "").strip()
        if instructions:
            system += "\n\nThe user's standing instructions:\n" + instructions
        (self.job / "system.txt").write_text(system)
        # A pasted picture travels as a file in the job folder, which is also the
        # folder the agent runs in, so it can read it.
        figures = ""
        if images and provider["kind"] != "api":
            for index, image in enumerate(images):
                (self.job / figure_name(index)).write_bytes(image.data)
            figures = "\n\n" + figures_prompt(images)
        if provider["kind"] == "api":
            body = self.core.call("chatRequest", options["model"], system, history, prompt)
            if images:
                # The picture travels inside the message, as an image_url block.
                body["messages"][-1]["content"] = image_content(prompt, images)
            (self.job / "request.json").write_text(json.dumps(body))
            text = ""
        elif provider.get("sessions"):
            text = prompt + figures
        else:
            text = self.core.call("statelessPrompt", system, history, prompt + figures)
        (self.job / "prompt.txt").write_text(text)
        self.core.start_output(invocation["format"])
        self.offset, self.partial, self.cancelled = 0, b"", False
        env = QProcessEnvironment.systemEnvironment()
        for key, value in {"TC_PROXY": self.config.values.get("proxy", "auto"),
                           "TC_STDIN": "1" if invocation["stdin"] else "0",
                           "TC_KEY_FILE": str(self.keys.path), "TC_PYTHON": sys.executable,
                           "AI_MODEL": options.get("model", "")}.items():
            env.insert(key, str(value))
        if provider.get("keyVar"):
            env.insert("TC_KEY_VAR", provider["keyVar"])
            env.insert("TC_KEY_ACCOUNT", provider["id"])
        else:
            env.insert("TC_KEY_VAR", "")
        env.insert("TC_BIN", options.get("bin", "").strip())
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(self.job))
        self.process.setStandardOutputFile(str(self.job / "runner-out.txt"))
        self.process.setStandardErrorFile(str(self.job / "runner-error.txt"))
        self.process.setProgram("/bin/bash")
        argv = invocation["argv"]
        if provider["kind"] == "api":
            argv += ["--connect-timeout", "20", "--max-time", "600"]
        self.process.setArguments([str(VENDOR / "run-agent.sh"), str(self.job), str(self.job), *argv])
        self.running = True
        self.runningChanged.emit(True)
        self.process.start()
        self.timer.start()

    def _drain(self, final=False):
        path = self.job / "out.txt"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
            self.offset += len(chunk)
            parts = (self.partial + chunk).split(b"\n")
            self.partial = parts.pop()
            if final and self.partial:
                parts.append(self.partial)
                self.partial = b""
            if parts:
                self.core.feed([line.decode("utf-8", errors="replace") for line in parts])

    def _poll(self):
        if self.running:
            self._drain()
            state = self.core.output()
            if self.invocation["format"] == "text" and self.partial:
                state["text"] += self.partial.decode("utf-8", errors="replace")
            self.updated.emit(state)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(127)

    def _finished(self, code, *_):
        if not self.running:
            return
        self.timer.stop()
        self._drain(final=True)
        exit_file = self.job / "exit"
        if exit_file.exists():
            try:
                code = int(exit_file.read_text())
            except ValueError:
                code = 1
        def read(name):
            file = self.job / name
            return file.read_text(errors="replace") if file.exists() else ""
        self.core.finish_output(code, read("err.txt") or read("runner-error.txt"),
            read(self.invocation.get("lastMessage", "last.txt")), self.provider.get("setup", ""))
        state = self.core.output()
        if self.cancelled:
            state["error"] = "Stopped."
        elif not state["error"] and not self.testing and state["sessionId"]:
            self.session_id = state["sessionId"]
            self.session_provider = self.provider["id"]
        self.running = False
        self.runningChanged.emit(False)
        self.finished.emit(state)
        shutil.rmtree(self.job, ignore_errors=True)
        self.job = None

    def cancel(self):
        if not self.running:
            return
        self.cancelled = True
        # The runner puts the tool and its descendants in their own process group.
        try:
            pid = int((self.job / "cpid").read_text())
            if pid > 1:
                os.killpg(pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass
        self.process.kill()
        self.process.waitForFinished(1000)


def document_context(doc, current_page, selection=None, max_chars=120000):
    """Extract nearby pages within a bound, with their original page numbers."""
    current_page = max(0, min(doc.page_count - 1, current_page))
    pages, used = {}, 0
    for step in range(doc.page_count):
        indexes = [current_page] if step == 0 else [current_page + step, current_page - step]
        for index in indexes:
            if not 0 <= index < doc.page_count:
                continue
            text = doc.page_text(index)
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining] + "\n[Page text truncated]"
            pages[index] = {"page": index + 1, "text": text}
            used += len(text) + 30
        if used >= max_chars:
            break
    context = dict(name=Path(doc.path).name, title=doc.title, pageCount=doc.page_count,
                   currentPage=current_page + 1, pages=[pages[i] for i in sorted(pages)])
    if selection:
        context["selection"] = selection
    return context


def proposed_annotation(doc, note):
    """Resolve an assistant's quote on its cited page, without fuzzy placement."""
    if note.get("invalid"):
        raise ValueError(note["invalid"])
    page_number = note.get("page")
    if not isinstance(page_number, int) or not 1 <= page_number <= doc.page_count:
        raise ValueError("The suggestion must cite a valid page number.")
    page = page_number - 1
    pdf_page = doc.doc[page]
    quote = note.get("quote", "").strip()
    rects = []
    if quote:
        normalized = " ".join(doc.page_text(page).split()).casefold()
        if normalized.count(" ".join(quote.split()).casefold()) > 1:
            raise ValueError("This quote appears more than once on the page. Ask for a longer, unique passage.")
        hits = pdf_page.search_for(quote)
        if not hits:
            raise ValueError("The exact quote was not found on the cited page. Ask the assistant to copy it exactly.")
        rects = [Rect(*tuple(hit * pdf_page.rotation_matrix)) for hit in hits]
    kind = note.get("type", "note")
    if kind not in ("note", "highlight", "underline", "strikeout"):
        raise ValueError("Unknown annotation type.")
    if kind != "note" and not rects:
        raise ValueError("A marking suggestion needs an exact quote.")
    if kind == "note" and not note.get("comment", "").strip():
        raise ValueError("The margin note has no text.")
    if not rects:
        x, y = doc.page_origin(page)
        rects = [Rect(x + 24, y + 24, x + 44, y + 44)]
    return Annotation(kind=AnnKind.MARGIN if kind == "note" else AnnKind(kind), page=page,
        rects=rects, text=note.get("comment", ""), color="#ffd54f", author="AI Assistant",
        extra={"capture": quote, "assistant": True})
