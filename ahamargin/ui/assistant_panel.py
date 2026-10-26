"""A native chat panel using heehawPDF's assistant protocol."""

from __future__ import annotations

from html import escape
import uuid

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from ..assistant import (
    Agent, AssistantConfig, AssistantCore, KeyStore, document_context,
    proposed_annotation, validate_options,
)

API_PRESETS = [
    ("MiniMax", "https://api.minimaxi.com/v1", "MiniMax-M2.7", False),
    ("MiniMax Global", "https://api.minimax.io/v1", "MiniMax-M2.7", False),
    ("DeepSeek", "https://api.deepseek.com", "deepseek-chat", False),
    ("Kimi", "https://api.moonshot.cn/v1", "kimi-k2-turbo-preview", False),
    ("Qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", False),
    ("GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-4.6", False),
    ("SiliconFlow", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3", False),
    ("OpenRouter", "https://openrouter.ai/api/v1", "deepseek/deepseek-chat", False),
    ("OpenAI", "https://api.openai.com/v1", "gpt-5-mini", False),
    ("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", False),
    ("Ollama (local)", "http://localhost:11434/v1", "qwen2.5", True),
    ("LM Studio (local)", "http://localhost:1234/v1", "", True),
    ("Other", "https://", "", False),
]


class AssistantSettingsDialog(QDialog):
    def __init__(self, panel, add_api=False):
        super().__init__(panel)
        self.panel = panel
        self.setWindowTitle("AI Assistant settings")
        self.resize(520, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.api_id = "api-" + uuid.uuid4().hex if add_api else None
        self.provider = panel.config.provider()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.preset = QComboBox()
        self.preset.addItems([p[0] for p in API_PRESETS])
        self.name = QLineEdit()
        if add_api:
            form.addRow("Starting point", self.preset)
        else:
            layout.addWidget(QLabel(self.provider["title"]))
        if add_api or self.provider.get("custom"):
            form.addRow("API name", self.name)
            self.name.setText(self.provider["title"] if not add_api else "")
        self.base = QLineEdit()
        self.model = QLineEdit()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Leave blank to keep the saved key")
        self.no_key = QCheckBox("No API key needed (local server)")
        self.binary = QLineEdit()
        self.binary.setPlaceholderText("Use the tool on your login shell's PATH")
        self.command = QLineEdit()
        self.command.setPlaceholderText("Prompt on stdin, answer on stdout")
        self.is_api = add_api or self.provider["kind"] == "api"
        if self.is_api:
            form.addRow("Base URL", self.base)
            form.addRow("Model", self.model)
            form.addRow("API key", self.key)
            form.addRow(self.no_key)
        else:
            form.addRow("Model (optional)", self.model)
            if self.provider["id"] == "custom":
                form.addRow("Command", self.command)
            else:
                form.addRow("Executable (optional)", self.binary)
                hint = QLabel(self.provider.get("setup", ""))
                hint.setWordWrap(True)
                form.addRow(hint)
        self.proxy = QComboBox()
        self.proxy.setEditable(True)
        self.proxy.addItems(["auto", "none"])
        self.proxy.setCurrentText(panel.config.values.get("proxy", "auto"))
        self.proxy.setToolTip("auto: shell/system proxy; none: direct; or enter a proxy URL")
        form.addRow("Proxy", self.proxy)
        self.instructions = QPlainTextEdit(panel.config.values.get("instructions", ""))
        self.instructions.setPlaceholderText("For example: answer in Chinese, preserve technical terms.")
        self.instructions.setMaximumHeight(100)
        form.addRow("Standing instructions", self.instructions)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        self.test_button = buttons.addButton("Save & Test", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.close)
        self.test_button.clicked.connect(self.test)
        if self.provider.get("custom") and not add_api:
            delete = buttons.addButton("Delete API", QDialogButtonBox.ButtonRole.DestructiveRole)
            delete.clicked.connect(self.delete_api)
        if self.is_api and not add_api:
            clear_key = buttons.addButton("Remove key", QDialogButtonBox.ButtonRole.ActionRole)
            clear_key.clicked.connect(self.remove_key)
        layout.addWidget(buttons)
        if add_api:
            self.preset.currentIndexChanged.connect(self.apply_preset)
            self.apply_preset(0)
        else:
            options = panel.config.options(self.provider)
            self.base.setText(options["baseURL"])
            self.model.setText(options["model"])
            self.binary.setText(options["bin"])
            self.command.setText(options["command"])
            self.no_key.setChecked(options["noKey"])
        self.no_key.toggled.connect(self.key.setDisabled)
        self.key.setDisabled(self.no_key.isChecked())

    def apply_preset(self, index):
        name, url, model, no_key = API_PRESETS[index]
        self.name.setText(name)
        self.base.setText(url)
        self.model.setText(model)
        self.no_key.setChecked(no_key)

    def save(self):
        options = dict(model=self.model.text().strip(), baseURL=self.base.text().strip(),
            bin=self.binary.text().strip(), command=self.command.text().strip(), noKey=self.no_key.isChecked())
        provider = self.provider
        try:
            if self.api_id:
                if not self.name.text().strip():
                    raise ValueError("Enter a name for this API.")
                provider = dict(id=self.api_id, title=self.name.text().strip(), kind="api", custom=True)
            validate_options(provider, options)
            key_status = ""
            if self.is_api and self.key.text().strip() and not options["noKey"]:
                key_status = self.panel.keys.set(provider["id"], self.key.text().strip())
                self.key.clear()
            if self.api_id:
                self.panel.config.values["customApis"].append(dict(id=self.api_id,
                    name=self.name.text().strip(), baseURL=options["baseURL"], model=options["model"],
                    noKey=options["noKey"]))
                self.api_id = None
            elif provider.get("custom"):
                if not self.name.text().strip():
                    raise ValueError("Enter a name for this API.")
                api = next(a for a in self.panel.config.values["customApis"] if a["id"] == provider["id"])
                api.update(name=self.name.text().strip(), baseURL=options["baseURL"],
                           model=options["model"], noKey=options["noKey"])
            self.panel.config.values["providers"][provider["id"]] = options
            self.panel.config.values["provider"] = provider["id"]
            self.panel.config.values["proxy"] = self.proxy.currentText().strip() or "auto"
            self.panel.config.values["instructions"] = self.instructions.toPlainText().strip()
            self.panel.config.save()
            self.provider = self.panel.config.provider(provider["id"])
            self.panel.reload_providers()
            self.status.setText("Saved. " + key_status)
            return True
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return False

    def test(self):
        if not self.save():
            return
        if self.panel.agent.running:
            self.status.setText("Stop the current reply before testing a connection.")
            return
        self.status.setText("Testing…")
        self.test_button.setEnabled(False)
        self.panel.connection_test_result.connect(self.test_finished)
        self.panel.test_connection(self.provider)

    def test_finished(self, state):
        self.panel.connection_test_result.disconnect(self.test_finished)
        self.test_button.setEnabled(True)
        self.status.setText(state.get("error") or ("Connected. Reply: " + state["text"].strip()[:200]))

    def delete_api(self):
        self.panel.keys.remove(self.provider["id"])
        self.panel.config.values["customApis"] = [a for a in self.panel.config.values["customApis"] if a["id"] != self.provider["id"]]
        self.panel.config.values["providers"].pop(self.provider["id"], None)
        self.panel.config.values["provider"] = "claude"
        self.panel.config.save()
        self.panel.reload_providers()
        self.close()

    def remove_key(self):
        self.panel.keys.remove(self.provider["id"])
        self.status.setText("Saved API key removed.")


class AssistantPanel(QWidget):
    annotationRequested = Signal(object)
    revealRequested = Signal(int, object)
    pageRequested = Signal(int)
    connection_test_result = Signal(object)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.core = AssistantCore()
        self.config = AssistantConfig(self.core, settings)
        self.keys = KeyStore()
        self.agent = Agent(self.core, self.config, self.keys, self)
        self.agent.updated.connect(self.on_update)
        self.agent.finished.connect(self.on_finished)
        self.agent.runningChanged.connect(self.update_buttons)
        self.doc, self.page, self.selection = None, 0, None
        self.messages, self.history, self.queue = [], [], []
        self.active_message = None
        self.testing = False
        self._provider_id = None
        self.error = ""
        self._build_ui()
        self.reload_providers()
        self.render()
        self.update_context()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        self.picker = QComboBox()
        self.picker.setMinimumContentsLength(12)
        self.picker.currentIndexChanged.connect(self.provider_changed)
        top.addWidget(self.picker, 1)
        settings_button = QPushButton("Settings…")
        settings_button.clicked.connect(lambda: self.open_settings())
        top.addWidget(settings_button)
        new_button = QPushButton("New chat")
        new_button.clicked.connect(self.new_chat)
        top.addWidget(new_button)
        layout.addLayout(top)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setPlaceholderText("Default model")
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model"))
        model_row.addWidget(self.model, 1)
        add_api = QPushButton("＋ Add API…")
        add_api.clicked.connect(lambda: self.open_settings(add_api=True))
        model_row.addWidget(add_api)
        layout.addLayout(model_row)
        self.context_label = QLabel()
        self.context_label.setWordWrap(True)
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.context_label)
        context_row = QHBoxLayout()
        self.selection_label = QLabel()
        self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        context_row.addWidget(self.selection_label, 1)
        self.clear_selection = QPushButton("Clear selection")
        self.clear_selection.clicked.connect(lambda: self.set_selection(None))
        context_row.addWidget(self.clear_selection)
        layout.addLayout(context_row)
        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self.follow_link)
        self.transcript.setMinimumWidth(300)
        layout.addWidget(self.transcript, 1)
        actions = [
            ("Summarize page", "Summarize {target}."),
            ("Summarize PDF", "Summarize this document, noting any pages omitted from the supplied context."),
            ("Explain", "Explain {target} clearly."),
            ("Key points", "List the key points in {target}."),
            ("Translate", "Translate {target}, choosing the target language from my standing instructions or asking me if unspecified."),
            ("Questions", "Suggest study questions about {target}."),
        ]
        self.quick_buttons = []
        for offset in (0, 3):
            row = QHBoxLayout()
            for label, request in actions[offset:offset + 3]:
                button = QPushButton(label)
                button.clicked.connect(lambda checked=False, template=request: self.submit(
                    template.replace("{target}", "the selected text" if self.selection else f"page {self.page + 1}")))
                self.quick_buttons.append(button)
                row.addWidget(button)
            layout.addLayout(row)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask about this PDF… (Ctrl+Enter to send)")
        self.input.setMaximumHeight(100)
        self.input.textChanged.connect(self.update_buttons)
        layout.addWidget(self.input)
        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        bottom.addWidget(self.status, 1)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        bottom.addWidget(self.stop_button)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(lambda: self.submit())
        bottom.addWidget(self.send_button)
        layout.addLayout(bottom)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        shortcut.activated.connect(lambda: self.submit())
        shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self.input)
        shortcut.activated.connect(lambda: self.submit())

    def reload_providers(self):
        selected = self.config.values["provider"]
        self.picker.blockSignals(True)
        self.picker.clear()
        for provider in self.config.providers():
            self.picker.addItem(provider["title"], provider["id"])
        self.picker.setCurrentIndex(max(0, self.picker.findData(selected)))
        self.picker.blockSignals(False)
        self.provider_changed()

    def provider_changed(self, *_):
        selected = self.picker.currentData()
        if not selected:
            return
        if self._provider_id is not None and self._provider_id != selected:
            self.new_chat()
        self._provider_id = selected
        self.config.values["provider"] = selected
        self.config.save()
        provider = self.config.provider()
        self.model.clear()
        self.model.addItems(provider.get("models", []))
        self.model.setEditText(self.config.options(provider)["model"])
        self.input.setPlaceholderText(f"Ask {provider['title']} about this PDF… (Ctrl+Enter)")
        self.update_buttons()

    def set_document(self, doc):
        self.new_chat()
        self.doc, self.page, self.selection = doc, 0, None
        self.update_context()
        self.update_buttons()

    def set_page(self, page):
        self.page = page
        self.update_context()

    def set_selection(self, selection):
        self.selection = selection
        self.update_context()

    def update_context(self):
        self.context_label.setText(f"{self.doc.title} · page {self.page + 1} of {self.doc.page_count}" if self.doc else "Open a PDF to ask about it.")
        self.selection_label.setText(f"Selection on page {self.selection['page']} · {len(self.selection['text'])} characters" if self.selection else "")
        self.clear_selection.setVisible(bool(self.selection))

    def set_theme(self, theme):
        self.transcript.document().setDefaultStyleSheet(
            f"body {{ color: {theme.text}; }} a {{ color: {theme.accent}; }} "
            f"blockquote {{ color: {theme.text_dim}; }} pre {{ background-color: {theme.surface_alt}; }}"
        )
        self.render()

    def new_chat(self):
        self.queue.clear()
        self.agent.cancel()
        self.agent.reset_session()
        self.messages.clear()
        self.history.clear()
        self.active_message = None
        self.error = ""
        self.render()
        self.update_buttons()

    def stop(self):
        self.queue.clear()
        self.agent.cancel()
        self.update_buttons()

    def open_settings(self, add_api=False):
        if self.agent.running:
            self.status.setText("Stop the current reply before changing settings.")
            return
        dialog = AssistantSettingsDialog(self, add_api=add_api)
        dialog.show()

    def submit(self, request=None):
        text = (request if request is not None else self.input.toPlainText()).strip()
        if not text or self.doc is None:
            return
        if self.testing:
            self.status.setText("Wait for the connection test to finish.")
            return
        if request is None:
            self.input.clear()
        entry = dict(text=text, page=self.page, selection=self.selection)
        if self.agent.running:
            self.queue.append(entry)
            self.update_buttons()
            return
        self._send(entry)

    def _send(self, entry):
        if self.doc is None:
            return
        provider = self.config.provider()
        options = self.config.options(provider)
        options["model"] = self.model.currentText().strip()
        self.config.values["providers"][provider["id"]] = options
        self.config.save()
        try:
            context = document_context(self.doc, entry["page"], entry["selection"])
            prompt = self.core.call("buildPrompt", context, entry["text"])
            included = [page["page"] for page in context["pages"]]
            if len(included) < self.doc.page_count:
                prompt += "\n\nOnly the pages marked above were supplied; do not invent content from omitted pages."
            self.error = ""
            self.agent.run(prompt, self.history, options=options)
        except (OSError, ValueError, RuntimeError) as exc:
            self.error = str(exc)
            self.render()
            self.update_buttons()
            return
        self.messages.append(dict(role="user", text=entry["text"]))
        self.active_message = dict(role="assistant", text="", error=None, added=set(), request=entry["text"])
        self.messages.append(self.active_message)
        self.render()
        self.update_buttons()

    def test_connection(self, provider):
        try:
            self.testing = True
            self.agent.run("Reply with the single word OK.", [], provider=provider, testing=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self.testing = False
            self.connection_test_result.emit(dict(error=str(exc), text=""))
        self.update_buttons()

    def on_update(self, state):
        if self.testing:
            return
        if self.active_message is not None:
            self.active_message.update(text=state["text"], error=state["error"])
            self.render()
        self.status.setText(state.get("activity") or ("Thinking…" if state.get("thinking") else "Answering…"))

    def on_finished(self, state):
        if self.testing:
            self.testing = False
            self.connection_test_result.emit(state)
        elif self.active_message is not None:
            self.active_message.update(text=state["text"], error=state["error"])
            if not state["error"]:
                self.history.extend([dict(role="user", content=self.active_message["request"]),
                                     dict(role="assistant", content=state["text"])])
                self.history = self.history[-20:]
            self.active_message = None
            self.render()
        self.update_buttons()
        if self.queue:
            QTimer.singleShot(0, self._next_queued)

    def _next_queued(self):
        if self.queue and not self.agent.running:
            self._send(self.queue.pop(0))

    def update_buttons(self, *_):
        has_doc = self.doc is not None
        self.send_button.setEnabled(has_doc and bool(self.input.toPlainText().strip()) and not self.testing)
        self.send_button.setText("Queue" if self.agent.running else "Send")
        self.stop_button.setEnabled(self.agent.running)
        self.picker.setEnabled(not self.agent.running)
        self.model.setEnabled(not self.agent.running)
        for button in self.quick_buttons:
            button.setEnabled(has_doc and not self.testing)
        self.status.setText(f"{len(self.queue)} message(s) queued" if self.queue else ("Answering…" if self.agent.running else ""))

    def render(self):
        scrollbar = self.transcript.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 30
        position = scrollbar.value()
        sections = []
        for message_index, message in enumerate(self.messages):
            if message["role"] == "user":
                sections.append("<h4>You</h4>" + self.core.render(message["text"]))
                continue
            sections.append("<h4>Assistant</h4>")
            streaming = message is self.active_message and self.agent.running
            for note_index, segment in enumerate(self.core.call("parseReply", message["text"], streaming)):
                if segment["kind"] == "text":
                    sections.append(self.core.render(segment["text"]))
                elif segment["kind"] == "note":
                    note = segment["note"]
                    title = "Margin note" if note["type"] == "note" else note["type"].title()
                    sections.append(f"<hr><b>{escape(title)} · p. {note.get('page') or '?'}</b>")
                    if note["quote"]:
                        sections.append(f"<blockquote>{escape(note['quote'])}</blockquote>")
                    if note["comment"]:
                        sections.append(self.core.render(note["comment"]))
                    if note.get("invalid"):
                        sections.append("<p>" + escape(note["invalid"]) + "</p>")
                    else:
                        label = "Added to PDF" if note_index in message["added"] else f'<a href="#add-{message_index}-{note_index}">Add to PDF</a>'
                        sections.append(f'<p>{label} &nbsp; <a href="#show-{message_index}-{note_index}">Show passage</a></p>')
                    sections.append("<hr>")
            if message.get("error"):
                sections.append("<p><b>" + escape(message["error"]).replace("\n", "<br>") + "</b></p>")
        if not sections:
            sections.append("<h3>Read with AI</h3><p>Ask questions, summarize, translate, or suggest highlights and margin notes.</p>"
                            "<p>Choose a tool above or add your API. Answers cite pages you can click. "
                            "Suggested notes are added only when you click <b>Add to PDF</b>.</p>")
        if self.error:
            sections.append("<p><b>" + escape(self.error) + "</b></p>")
        self.transcript.setHtml("".join(sections))
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def follow_link(self, url):
        fragment = url.fragment()
        try:
            if fragment.startswith("page-"):
                page = int(fragment[5:]) - 1
                if self.doc and 0 <= page < self.doc.page_count:
                    self.pageRequested.emit(page)
            elif fragment.startswith(("add-", "show-")) and self.doc:
                action, message_index, note_index = fragment.split("-")
                message = self.messages[int(message_index)]
                index = int(note_index)
                segment = self.core.call("parseReply", message["text"], False)[index]
                if segment["kind"] != "note":
                    return
                annotation = proposed_annotation(self.doc, segment["note"])
                if action == "add" and index not in message["added"]:
                    self.annotationRequested.emit(annotation)
                    message["added"].add(index)
                self.revealRequested.emit(annotation.page, annotation.bounds)
                self.error = ""
                self.render()
            elif url.scheme() in ("https", "http"):
                QDesktopServices.openUrl(url)
        except (ValueError, IndexError, KeyError) as exc:
            self.error = str(exc)
            self.render()
