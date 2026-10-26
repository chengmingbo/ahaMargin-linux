"""Verify that renaming the app preserves existing preferences."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings

from ahamargin import APP_NAME, ORGANIZATION_NAME
from ahamargin.ui.settings import application_settings


class SettingsMigrationTest(unittest.TestCase):
    def test_migrates_once_without_overwriting_new_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, tmp)
            QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.SystemScope, tmp)
            legacy = QSettings("MarginNote Linux", "MarginNote for Linux")
            legacy.setValue("recent/files", ["/tmp/existing.pdf"])
            legacy.setValue("print/page_size", "Letter")
            legacy.sync()
            current = QSettings(ORGANIZATION_NAME, APP_NAME)
            current.setValue("print/page_size", "A4")
            current.sync()

            settings = application_settings()
            self.assertEqual(settings.value("recent/files"), ["/tmp/existing.pdf"])
            self.assertEqual(settings.value("print/page_size"), "A4")
            self.assertEqual(legacy.value("print/page_size"), "Letter")

            settings.remove("recent/files")
            settings.sync()
            self.assertFalse(application_settings().contains("recent/files"))


if __name__ == "__main__":
    unittest.main()
