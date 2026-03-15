class TestFlashcard:
    def test_create_flashcard(self, sample_flashcard):
        assert sample_flashcard.front == "Qual é a capital da França?"
        assert sample_flashcard.back == "Paris"
        assert "geografia" in sample_flashcard.tags

    def test_to_anki_format(self, sample_flashcard):
        result = sample_flashcard.to_anki_format()
        assert "Paris" in result
        assert "geografia" in result
        assert "\t" in result


class TestDeck:
    def test_create_deck(self, sample_deck):
        assert sample_deck.name == "Geografia"
        assert sample_deck.total_cards == 0

    def test_add_flashcard(self, sample_deck, sample_flashcard):
        sample_deck.add_flashcard(sample_flashcard)
        assert sample_deck.total_cards == 1
        assert sample_deck.flashcards[0] == sample_flashcard

    def test_total_cards_property(self, deck_with_cards):
        assert deck_with_cards.total_cards == 2
