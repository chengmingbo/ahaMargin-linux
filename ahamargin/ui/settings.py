"""Application preferences, including migration from the previous name."""

from PySide6.QtCore import QSettings

from .. import APP_NAME, ORGANIZATION_NAME


def application_settings() -> QSettings:
    settings = QSettings(ORGANIZATION_NAME, APP_NAME)
    if not settings.value("migration/marginnote", False, type=bool):
        legacy = QSettings("MarginNote Linux", "MarginNote for Linux")
        for key in legacy.allKeys():
            if not settings.contains(key):
                settings.setValue(key, legacy.value(key))
        settings.setValue("migration/marginnote", True)
        settings.sync()
    return settings
