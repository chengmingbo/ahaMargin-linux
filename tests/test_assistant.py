"""Integration checks for the native assistant, using fake CLI/API providers.

No external AI service or credentials are used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TEST_HOME = tempfile.TemporaryDirectory(prefix="ahamargin-ai-tests-")
os.environ["XDG_CONFIG_HOME"] = TEST_HOME.name + "/config"
os.environ["XDG_DATA_HOME"] = TEST_HOME.name + "/data"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf
from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ahamargin.assistant import (
    Agent, AssistantConfig, AssistantCore, AssistantImage, KeyStore, document_context,
    proposed_annotation,
)
from ahamargin.core.annotations import AnnKind
from ahamargin.core.document import PdfDocument
from ahamargin.ui.assistant_panel import AssistantSettingsDialog, IMAGE_REQUEST
from ahamargin.ui.main_window import MainWindow

ANSWER = '''The main finding is on [p. 2].
<note type="highlight" page="2"><quote>A unique finding about carbon.</quote><comment>Key finding</comment></note>
<note type="note" page="1"><comment>Compare these results.</comment></note>'''


def wait_for(predicate, timeout=10000):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("Timed out waiting for assistant")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append((self.path, dict(self.headers), body))
        if self.path.startswith("/fail"):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Test authentication failure"}}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in ("Reading… ", ANSWER[:35], ANSWER[35:]):
            event = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
            data = ("data: " + event + "\n\n").encode()
            # Split UTF-8 and JSON across writes to exercise incremental parsing.
            for index in range(0, len(data), 7):
                self.wfile.write(data[index:index + 7])
                self.wfile.flush()
            time.sleep(0.03)
        self.wfile.write(b'data: [DONE]\n\n')

    def log_message(self, *_):
        pass


class AssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.core = AssistantCore()
        self.config = AssistantConfig(self.core, QSettings(str(self.path / "settings.ini"), QSettings.Format.IniFormat))
        self.keys = KeyStore(self.path / "keys")
        self.agent = Agent(self.core, self.config, self.keys)
        self.addCleanup(self.agent.cancel)
        self.states = []
        self.agent.finished.connect(self.states.append)
        self.shell = patch.dict(os.environ, {"SHELL": "/bin/sh"})
        self.shell.start()
        self.addCleanup(self.shell.stop)
        pdf_path = self.path / "sample.pdf"
        with pymupdf.open() as pdf:
            for text in ("Context from page one.", "A unique finding about carbon.", "Repeated quote. Repeated quote."):
                page = pdf.new_page()
                page.insert_text((60, 120), text)
            pdf[1].set_rotation(90)
            pdf.save(pdf_path)
        self.doc = PdfDocument(str(pdf_path))
        self.addCleanup(self.doc.close)

    def api(self, fail=False, key=False):
        provider = dict(id="test-api", title="Test API", kind="api", keyVar="AI_API_KEY" if key else None)
        base = f"http://127.0.0.1:{self.server.server_port}" + ("/fail" if fail else "/v1")
        return provider, dict(model="test-model", baseURL=base, noKey=not key)

    def window(self):
        window = MainWindow(self.doc.path)
        self.addCleanup(lambda: self.close_window(window))
        window.show()
        window.show_assistant()
        self.app.processEvents()
        return window

    @staticmethod
    def close_window(window):
        window.assistant_panel.stop()
        if window.store:
            window.store.dirty = False
        window.close()

    def test_prompt_context_is_bounded_and_preserves_page_and_selection(self):
        context = document_context(self.doc, 1, {"page": 2, "text": "unique finding"}, max_chars=80)
        prompt = self.core.call("buildPrompt", context, "Explain")
        self.assertIn("=== Page 2 ===", prompt)
        self.assertIn('<selection page="2">', prompt)
        self.assertIn("<current_page>2</current_page>", prompt)
        self.assertLess(sum(len(page["text"]) for page in context["pages"]), 120)
        self.assertIn("ahaMargin-linux", self.core.system_prompt)

    def test_streaming_api_and_http_errors(self):
        provider, options = self.api()
        updates = []
        self.agent.updated.connect(updates.append)
        self.agent.run("Explain", [], provider, options)
        wait_for(lambda: self.states)
        self.assertIsNone(self.states[-1]["error"])
        self.assertEqual(self.states[-1]["text"], "Reading… " + ANSWER)
        request = self.server.requests[-1]
        self.assertEqual(request[0], "/v1/chat/completions")
        self.assertTrue(request[2]["stream"])
        self.assertTrue(updates or not self.agent.running)
        self.assertIsNone(self.agent.job)

        provider, options = self.api(fail=True)
        self.agent.run("Explain", [], provider, options)
        wait_for(lambda: len(self.states) == 2)
        self.assertIn("HTTP 401", self.states[-1]["error"])
        self.assertIn("Test authentication failure", self.states[-1]["error"])

    def test_key_fallback_is_private_and_reaches_api_header_only(self):
        key = 'test-key-with-quote"and-backslash\\'
        with patch("ahamargin.assistant.shutil.which", return_value=None):
            self.keys.set("test-api", key)
        self.assertEqual(self.keys.path.stat().st_mode & 0o777, 0o600)
        provider, options = self.api(key=True)
        self.agent.run("Hello", [], provider, options)
        wait_for(lambda: self.states)
        self.assertIsNone(self.states[-1]["error"])
        self.assertEqual(self.server.requests[-1][1].get("Authorization"), "Bearer " + key)
        self.assertNotIn(key, self.config.settings.value("ai/config", ""))
        self.keys.remove("test-api")
        self.assertNotIn("test-api", json.loads(self.keys.path.read_text()))

    def test_custom_cli_receives_context_and_history(self):
        script = self.path / "fake_cli.py"
        snapshot = self.path / "prompt.txt"
        script.write_text("import pathlib,sys\npathlib.Path(sys.argv[1]).write_text(sys.stdin.read())\nprint('CLI answer [p. 2]')\n")
        options = dict(command=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {shlex.quote(str(snapshot))}", model="")
        self.agent.run("PDF context here", [{"role": "user", "content": "Earlier question"}], self.config.provider("custom"), options)
        wait_for(lambda: self.states)
        self.assertEqual(self.states[-1]["text"], "CLI answer [p. 2]")
        self.assertIn("Earlier question", snapshot.read_text())
        self.assertIn("PDF context here", snapshot.read_text())

    def test_missing_executable_has_actionable_error(self):
        self.agent.run("Hello", [], self.config.provider("codex"), {"model": "", "bin": "/nonexistent/ahamargin-tool"})
        wait_for(lambda: self.states)
        self.assertIn("not found", self.states[-1]["error"])

    def test_pasted_picture_travels_with_the_message_to_an_api(self):
        provider, options = self.api()
        self.agent.run("What is this?", [], provider, options,
                      images=[AssistantImage(b"\xff\xd8\xff\xd9", "image/jpeg")])
        wait_for(lambda: self.states)
        self.assertIsNone(self.states[-1]["error"])
        content = self.server.requests[-1][2]["messages"][-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "What is this?"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_pasted_picture_is_written_next_to_the_request_for_a_cli_agent(self):
        script, snapshot = self.path / "claude", self.path / "job.json"
        script.write_text(f"#!{sys.executable}\nimport json,os,pathlib,sys\n"
            f"stdin=sys.stdin.read()\n"
            f"pathlib.Path({str(snapshot)!r}).write_text(json.dumps("
            "{'argv':sys.argv[1:],'files':sorted(os.listdir('.')),'stdin':stdin}))\n"
            "print(json.dumps({'type':'result','subtype':'success','result':'Seen the picture','session_id':'fake-session'}))\n")
        script.chmod(0o700)
        options = {"model": "sonnet", "bin": str(script)}
        self.agent.run("What is this?", [], self.config.provider("claude"), options,
                      images=[AssistantImage(b"\xff\xd8\xff\xd9", "image/jpeg")])
        wait_for(lambda: self.states)
        self.assertIsNone(self.states[-1]["error"])
        job = json.loads(snapshot.read_text())
        argv = job["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Read")
        self.assertIn("figure-1.jpg", job["files"])
        self.assertIn("figure-1.jpg", job["stdin"])

    def test_claude_session_resumes_without_replaying_system_prompt_as_text(self):
        script, snapshot = self.path / "claude", self.path / "arguments.json"
        script.write_text(f"#!{sys.executable}\nimport json,pathlib,sys\n"
            f"pathlib.Path({str(snapshot)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            "sys.stdin.read()\nprint(json.dumps({'type':'result','subtype':'success','result':'Session reply','session_id':'fake-session'}))\n")
        script.chmod(0o700)
        options = {"model": "sonnet", "bin": str(script)}
        self.agent.run("First question", [], self.config.provider("claude"), options)
        wait_for(lambda: self.states)
        self.assertIsNone(self.states[-1]["error"])
        self.assertIn("--session-id", json.loads(snapshot.read_text()))
        self.agent.run("Second question", [], self.config.provider("claude"), options)
        wait_for(lambda: len(self.states) == 2)
        args = json.loads(snapshot.read_text())
        self.assertIn("--resume", args)
        self.assertEqual(args[-1], "fake-session")

    def test_switching_documents_cancels_reply_and_clears_queued_context(self):
        window = self.window()
        panel = window.assistant_panel
        panel.config.values["providers"]["custom"] = {"command": "sleep 30"}
        panel.picker.setCurrentIndex(panel.picker.findData("custom"))
        panel.set_selection({"page": 2, "text": "original context"})
        panel.submit("First question")
        panel.submit("Queued question")
        self.assertTrue(panel.agent.running)
        window.close_document()
        self.assertFalse(panel.agent.running)
        self.assertEqual(panel.queue, [])
        self.assertEqual(panel.messages, [])
        self.assertEqual(panel.history, [])
        self.assertIsNone(panel.selection)

    def test_cancel_stops_tool_descendants_and_removes_job(self):
        script, pidfile = self.path / "sleep.py", self.path / "child.pid"
        script.write_text("import pathlib,subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\npathlib.Path(sys.argv[1]).write_text(str(p.pid))\nprint('started',flush=True)\ntime.sleep(60)\n")
        options = {"command": f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {shlex.quote(str(pidfile))}"}
        self.agent.run("Hello", [], self.config.provider("custom"), options)
        wait_for(pidfile.exists)
        job = self.agent.job
        pid = int(pidfile.read_text())
        try:
            self.agent.cancel()
            wait_for(lambda: self.states)
            self.assertEqual(self.states[-1]["error"], "Stopped.")
            proc = Path(f"/proc/{pid}/stat")
            wait_for(lambda: not proc.exists() or proc.read_text().split()[2] == "Z")
            self.assertFalse(job.exists())
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_annotations_require_exact_unique_quotes_and_handle_rotation(self):
        note = dict(type="highlight", page=2, quote="A unique finding about carbon.", comment="Finding")
        annotation = proposed_annotation(self.doc, note)
        original = self.doc.doc[1].search_for(note["quote"])[0]
        rotated = original * self.doc.doc[1].rotation_matrix
        self.assertEqual(annotation.rects[0].as_tuple(), tuple(rotated))
        self.assertEqual(annotation.page, 1)
        for invalid in (dict(note, page=9), dict(note, quote="invented"),
                        dict(note, page=3, quote="Repeated quote.")):
            with self.assertRaises(ValueError):
                proposed_annotation(self.doc, invalid)

    def test_gui_suggestions_are_explicit_and_support_undo_and_save(self):
        window = self.window()
        panel = window.assistant_panel
        panel.messages.append(dict(role="assistant", text=ANSWER, error=None, added=set()))
        panel.render()
        self.assertEqual(len(window.store.annotations), 0)
        panel.follow_link(QUrl("#page-2"))
        self.assertEqual(panel.page, 1)
        panel.follow_link(QUrl("#add-0-1"))
        self.assertEqual(len(window.store.annotations), 1)
        self.assertEqual(window.store.annotations[0].kind, AnnKind.HIGHLIGHT)
        self.assertEqual(window.view._flash[0], 1)
        panel.follow_link(QUrl("#add-0-1"))
        self.assertEqual(len(window.store.annotations), 1)
        window.undo()
        self.assertEqual(len(window.store.annotations), 0)
        window.redo()
        panel.follow_link(QUrl("#add-0-2"))
        self.assertEqual(len(window.store.margin_notes()), 1)
        window.save()
        self.assertFalse(window.store.dirty)
        window.close_document()
        window.open_path(self.doc.path)
        self.assertEqual(len(window.store.annotations), 2)
        self.assertEqual(panel.messages, [])
        self.assertIsNone(panel.selection)

    def test_gui_api_queue_keeps_history_and_selection(self):
        window = self.window()
        panel = window.assistant_panel
        provider, options = self.api()
        panel.config.values["customApis"].append(dict(id=provider["id"], name="Test API", baseURL=options["baseURL"], model=options["model"], noKey=True))
        panel.config.values["provider"] = provider["id"]
        panel.reload_providers()
        panel.set_selection({"page": 2, "text": "unique finding"})
        panel.submit("First question")
        panel.submit("Second question")
        wait_for(lambda: len(panel.history) == 4 and not panel.agent.running)
        messages = self.server.requests[-1][2]["messages"]
        self.assertTrue(any(m["content"] == "First question" for m in messages))
        self.assertIn('<selection page="2">', messages[-1]["content"])
        self.assertIn("=== Page 2 ===", messages[-1]["content"])
        self.assertIn("Second question", messages[-1]["content"])

    def test_pasting_a_picture_attaches_it_and_sends_it(self):
        window = self.window()
        panel = window.assistant_panel
        provider, options = self.api()
        panel.config.values["customApis"].append(dict(id=provider["id"], name="Test API", baseURL=options["baseURL"], model=options["model"], noKey=True))
        panel.config.values["provider"] = provider["id"]
        panel.reload_providers()
        image = QImage(320, 200, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        QGuiApplication.clipboard().setImage(image)
        panel.input.paste()
        self.assertEqual(len(panel.pending), 1)
        self.assertTrue(panel.attachments.isVisibleTo(panel))
        panel.input.setPlainText("What is this?")
        panel.submit()
        wait_for(lambda: not panel.agent.running and panel.history)
        content = self.server.requests[-1][2]["messages"][-1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(panel.pending, [])
        # The picture itself is not kept in the conversation.
        self.assertNotIn("base64", json.dumps(panel.history))
        # A picture on its own is a question, too.
        QGuiApplication.clipboard().setImage(image)
        panel.input.paste()
        panel.submit()
        wait_for(lambda: len(panel.history) == 4 and not panel.agent.running)
        content = self.server.requests[-1][2]["messages"][-1]["content"]
        self.assertIn(IMAGE_REQUEST, content[0]["text"])
        self.assertEqual(content[1]["type"], "image_url")

    def test_settings_save_named_api_and_test_without_pdf_context(self):
        window = self.window()
        panel = window.assistant_panel
        dialog = AssistantSettingsDialog(panel, add_api=True)
        self.addCleanup(dialog.close)
        dialog.name.setText("My local API")
        dialog.base.setText(self.api()[1]["baseURL"])
        dialog.model.setText("test-model")
        dialog.no_key.setChecked(True)
        dialog.test()
        wait_for(lambda: not panel.agent.running and "Connected" in dialog.status.text())
        self.assertEqual(panel.config.provider()["title"], "My local API")
        body = self.server.requests[-1][2]
        self.assertNotIn("=== Page", body["messages"][-1]["content"])
        self.assertEqual(panel.history, [])
        self.assertEqual(panel.messages, [])
        dialog.delete_api()
        self.assertNotIn("My local API", [p["title"] for p in panel.config.providers()])


if __name__ == "__main__":
    unittest.main(verbosity=2)
