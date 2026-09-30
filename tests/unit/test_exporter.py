import csv
import json

from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.integrations.deck_exporter import DeckExporter


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
        output_path.write_bytes(b"previous export\n")
        DeckExporter.export_csv(deck_with_cards, output_path)

        with open(output_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows == [
            ["Qual é a capital da França?", "Paris"],
            ["Quando foi a Revolução Francesa?", "1789"],
        ]

    def test_export_csv_preserves_unicode_delimiters_and_math(self, tmp_path):
        front = 'Qual é o valor de "x, y"?\nUse $x^2$.'
        back = 'A resposta inclui café, "aspas" e duas linhas.\nFim.'
        deck = Deck(
            name="Matemática",
            flashcards=[Flashcard(front=front, back=back)],
        )
        output_path = tmp_path / "math.csv"

        DeckExporter.export_csv(deck, output_path)

        with output_path.open(encoding="utf-8", newline="") as file_obj:
            rows = list(csv.reader(file_obj))

        assert rows == [
            [
                'Qual é o valor de "x, y"?\nUse \\(x^2\\).',
                back,
            ]
        ]

    def test_export_csv_accepts_empty_deck_and_replaces_existing_file(
        self, tmp_path
    ):
        output_path = tmp_path / "empty.csv"
        output_path.write_bytes(b"previous export\n")
        deck = Deck(name="Empty", flashcards=[])

        DeckExporter.export_csv(deck, output_path)

        assert output_path.exists()
        assert output_path.read_bytes() == b""

    def test_export_anki(self, deck_with_cards, tmp_path):
        output_path = tmp_path / "test.txt"
        DeckExporter.export_anki(deck_with_cards, output_path)

        with open(output_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f, delimiter="\t"))

        assert rows[0] == ["# Deck: História"]
        assert rows[3] == ["#separator:tab"]
        assert rows[4] == ["#html:true"]
        assert [row for row in rows if len(row) == 2] == [
            ["Qual é a capital da França?", "Paris"],
            ["Quando foi a Revolução Francesa?", "1789"],
        ]

    def test_exporters_preserve_delimited_fields_as_two_columns(
        self, tmp_path
    ):
        front = "Question\tcontinued"
        back = "Answer line 1\nAnswer line 2"
        deck = Deck(
            name="Delimited",
            flashcards=[Flashcard(front=front, back=back)],
        )
        csv_path = tmp_path / "deck.csv"
        anki_path = tmp_path / "deck.txt"

        DeckExporter.export_csv(deck, csv_path)
        DeckExporter.export_anki(deck, anki_path)

        with open(csv_path, newline="", encoding="utf-8") as f:
            csv_rows = list(csv.reader(f))
        with open(anki_path, newline="", encoding="utf-8") as f:
            anki_rows = list(csv.reader(f, delimiter="\t"))

        assert csv_rows == [[front, back]]
        assert [row for row in anki_rows if len(row) == 2] == [[front, back]]

    def test_export_markdown(self, deck_with_cards, tmp_path):
        output_path = tmp_path / "test.md"
        DeckExporter.export_markdown(deck_with_cards, output_path)

        assert output_path.exists()
        content = output_path.read_text()
        assert "# História" in content
        assert "**Total:** 2 cards" in content
