from unittest.mock import patch

from flashcards_generator.delivery.web import main as web_main
from flashcards_generator.delivery.web.config import WebSettings


def test_main_starts_uvicorn_with_configured_host_and_port() -> None:
    settings = WebSettings(host="127.0.0.1", port=8765)
    with (
        patch.object(web_main, "get_settings", return_value=settings),
        patch.object(web_main.uvicorn, "run") as run,
    ):
        web_main.main()

    run.assert_called_once_with(web_main.app, host="127.0.0.1", port=8765)
