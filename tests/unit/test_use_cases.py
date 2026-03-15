from unittest.mock import MagicMock, patch

from flashcards_generator.application.use_cases import GenerateFlashcardsUseCase
from flashcards_generator.domain.entities import Flashcard


class TestGenerateFlashcardsUseCase:
    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    def test_execute_no_folders(self, mock_find, mock_client_class, sample_config):
        mock_find.return_value = "notebooklm"
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert result == []

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    def test_execute_with_folders_no_pdfs(
        self, mock_find, mock_client_class, sample_config
    ):
        mock_find.return_value = "notebooklm"
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        (sample_config.input_dir / "tema1").mkdir()

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert len(result) == 0

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    @patch("flashcards_generator.application.use_cases.ClozeConverter")
    @patch("flashcards_generator.application.use_cases.DeckExporter")
    def test_execute_successful_processing(
        self,
        mock_exporter_class,
        mock_converter_class,
        mock_find,
        mock_client_class,
        sample_config,
    ):
        mock_find.return_value = "notebooklm"

        mock_client = MagicMock()
        mock_client.create_notebook.return_value = "nb123"
        mock_client.add_source.return_value = "src456"
        mock_client.wait_for_source.return_value = True
        mock_client.generate_flashcards.return_value = "art789"
        mock_client.wait_for_artifact.return_value = True
        mock_client.download_flashcards.return_value = True
        mock_client.parse_flashcards.return_value = [
            Flashcard(front="Q1?", back="A1"),
            Flashcard(front="Q2?", back="A2"),
        ]
        mock_client_class.return_value = mock_client

        mock_converter = MagicMock()
        mock_converter.convert.side_effect = lambda x: Flashcard(
            front=f"{x.front} {{c1::{x.back}}}", back=x.back, tags=[]
        )
        mock_converter_class.return_value = mock_converter

        mock_exporter = MagicMock()
        mock_exporter_class.return_value = mock_exporter

        tema_dir = sample_config.input_dir / "Historia"
        tema_dir.mkdir()
        (tema_dir / "aula1.pdf").touch()

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert len(result) == 1
        assert result[0].name == "Historia_aula1"
        mock_exporter.export_csv.assert_called_once()

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    def test_execute_add_source_failure(
        self, mock_find, mock_client_class, sample_config
    ):
        mock_find.return_value = "notebooklm"

        mock_client = MagicMock()
        mock_client.create_notebook.return_value = "nb123"
        mock_client.add_source.side_effect = Exception("Add source error")
        mock_client_class.return_value = mock_client

        tema_dir = sample_config.input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").touch()

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert len(result) == 0

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    def test_execute_generate_flashcards_failure(
        self, mock_find, mock_client_class, sample_config
    ):
        mock_find.return_value = "notebooklm"

        mock_client = MagicMock()
        mock_client.create_notebook.return_value = "nb123"
        mock_client.add_source.return_value = "src456"
        mock_client.wait_for_source.return_value = True
        mock_client.generate_flashcards.return_value = None
        mock_client_class.return_value = mock_client

        tema_dir = sample_config.input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").touch()

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert len(result) == 0

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    @patch("flashcards_generator.application.use_cases.ClozeConverter")
    @patch("flashcards_generator.application.use_cases.DeckExporter")
    def test_execute_no_wait_mode(
        self,
        mock_exporter_class,
        mock_converter_class,
        mock_find,
        mock_client_class,
        sample_config,
    ):
        mock_find.return_value = "notebooklm"

        mock_client = MagicMock()
        mock_client.create_notebook.return_value = "nb123"
        mock_client.add_source.return_value = "src456"
        mock_client.wait_for_source.return_value = True
        mock_client.generate_flashcards.return_value = "art789"
        mock_client_class.return_value = mock_client

        sample_config.wait_for_completion = False

        tema_dir = sample_config.input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").touch()

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert len(result) == 1
        assert result[0].flashcards == []

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    @patch("flashcards_generator.application.use_cases.ClozeConverter")
    @patch("flashcards_generator.application.use_cases.DeckExporter")
    def test_execute_wait_timeout(
        self,
        mock_exporter_class,
        mock_converter_class,
        mock_find,
        mock_client_class,
        sample_config,
    ):
        mock_find.return_value = "notebooklm"

        mock_client = MagicMock()
        mock_client.create_notebook.return_value = "nb123"
        mock_client.add_source.return_value = "src456"
        mock_client.wait_for_source.return_value = True
        mock_client.generate_flashcards.return_value = "art789"
        mock_client.wait_for_artifact.return_value = False
        mock_client_class.return_value = mock_client

        tema_dir = sample_config.input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").touch()

        use_case = GenerateFlashcardsUseCase(sample_config)
        result = use_case.execute()

        assert len(result) == 1
        assert result[0].notebook_id == "nb123"

    @patch("flashcards_generator.application.use_cases.NotebookLMClient")
    @patch("flashcards_generator.application.use_cases.find_notebooklm")
    @patch("flashcards_generator.application.use_cases.ClozeConverter")
    @patch("flashcards_generator.application.use_cases.DeckExporter")
    def test_execute_custom_instructions(
        self,
        mock_exporter_class,
        mock_converter_class,
        mock_find,
        mock_client_class,
        sample_config,
    ):
        mock_find.return_value = "notebooklm"

        mock_client = MagicMock()
        mock_client.create_notebook.return_value = "nb123"
        mock_client.add_source.return_value = "src456"
        mock_client.wait_for_source.return_value = True
        mock_client.generate_flashcards.return_value = "art789"
        mock_client.wait_for_artifact.return_value = True
        mock_client.download_flashcards.return_value = True
        mock_client.parse_flashcards.return_value = []
        mock_client_class.return_value = mock_client

        sample_config.instructions = "Instruções customizadas"

        tema_dir = sample_config.input_dir / "Tema1"
        tema_dir.mkdir()
        (tema_dir / "file.pdf").touch()

        use_case = GenerateFlashcardsUseCase(sample_config)
        use_case.execute()

        call_args = mock_client.generate_flashcards.call_args
        assert call_args[1]["instructions"] == "Instruções customizadas"
