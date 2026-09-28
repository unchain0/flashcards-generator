from __future__ import annotations

from flashcards_generator.delivery.web.config import WebSettings


def test_web_settings_do_not_configure_a_server_notebooklm_home() -> None:
    assert "notebooklm_home" not in WebSettings.model_fields
