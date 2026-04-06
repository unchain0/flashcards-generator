import json

from flashcards_generator.application.exporter import DeckExporter


class TestDeckExporter:
    def test_export_json(self, deck_with_cards, tmp_path):
        output_path = tmp_path / "test.json"
        DeckExporter.export_json(deck_with_cards, output_path)

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert data["name"] == "História"
        assert len(data["flashcards"]) == 2

    def test_export_csv(self, deck_with_cards, tmp_path):
        output_path = tmp_path / "test.csv"
        DeckExporter.export_csv(deck_with_cards, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert '"Front","Back","Tags"' not in content
        assert "Paris" in content or "1789" in content

        # Verify only 2 columns (no tags column)
        lines = content.strip().split("\n")
        for line in lines:
            # Count quoted fields (CSV format)
            field_count = line.count('","') + 1
            assert field_count == 2, f"Expected 2 columns, got {field_count}"

    def test_export_anki(self, deck_with_cards, tmp_path):
        output_path = tmp_path / "test.txt"
        DeckExporter.export_anki(deck_with_cards, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "#separator:tab" in content
        assert "# Deck: História" in content
        assert "#tags column:" not in content

        # Verify only 2 columns (tab-separated, no trailing tab)
        lines = [
            line for line in content.split("\n") if line and not line.startswith("#")
        ]
        for line in lines:
            field_count = len(line.split("\t"))
            assert field_count == 2, f"Expected 2 columns, got {field_count}"

    def test_export_markdown(self, deck_with_cards, tmp_path):
        output_path = tmp_path / "test.md"
        DeckExporter.export_markdown(deck_with_cards, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "# História" in content
        assert "**Total:** 2 cards" in content
