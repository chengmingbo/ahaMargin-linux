# Changelog

## 1.1.0

- Add a native AI Assistant panel using heehawPDF's provider and response logic.
- Support Claude, Codex, OpenCode, DeepSeek, Kimi, Qwen Code, Gemini, custom
  commands, and saved OpenAI-compatible APIs, including local model servers.
- Include PDF text, page markers, the current page, and selected text in context.
- Stream replies with clickable citations, quick reading actions, queued
  messages, cancellation, and Claude session resumption.
- Offer proposed highlights, underlines, strikeouts, and linked margin notes.
  Explicitly adding a suggestion validates its quote and supports undo and save.
- Add keyring storage with a private-file fallback, proxy settings, standing
  instructions, and Save & Test without PDF content.
- Add fake-provider integration tests and include the upstream license notices.

## 1.0.1

- Rename the application to ahaMargin-linux, matching the macOS ahaMargin name.
- Update the Python package, command, desktop entry, exports, and documentation.
- Preserve existing PDF notes and migrate recent files and print preferences.
- Fix drawing previews appearing far from the cursor before mouse release.
  Rectangles, ellipses, lines, arrows, and ink now follow zoom and scrolling.
- Add a local installer and regression checks for drawing and settings migration.
