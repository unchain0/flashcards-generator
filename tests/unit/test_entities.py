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

    def test_deduplicate_empty_deck(self, sample_deck):
        removed = sample_deck.deduplicate()
        assert removed == 0
        assert sample_deck.total_cards == 0

    def test_deduplicate_no_duplicates(self, sample_deck):
        from flashcards_generator.domain.entities import Flashcard

        card1 = Flashcard(front="What is Python?", back="A programming language")
        card2 = Flashcard(front="What is Java?", back="Another language")
        sample_deck.add_flashcard(card1)
        sample_deck.add_flashcard(card2)

        removed = sample_deck.deduplicate()
        assert removed == 0
        assert sample_deck.total_cards == 2

    def test_deduplicate_exact_duplicates(self, sample_deck):
        from flashcards_generator.domain.entities import Flashcard

        card1 = Flashcard(front="What is Python?", back="A language")
        card2 = Flashcard(front="What is Python?", back="Different description")
        sample_deck.add_flashcard(card1)
        sample_deck.add_flashcard(card2)

        removed = sample_deck.deduplicate()
        assert removed == 1
        assert sample_deck.total_cards == 1

    def test_deduplicate_similar_duplicates(self, sample_deck):
        from flashcards_generator.domain.entities import Flashcard

        card1 = Flashcard(
            front="The Python programming language was created by Guido",
            back="van Rossum",
        )
        card2 = Flashcard(
            front="The Python programming language was created by Guido",
            back="van Rossum in 1991",
        )
        sample_deck.add_flashcard(card1)
        sample_deck.add_flashcard(card2)

        removed = sample_deck.deduplicate(similarity_threshold=0.9)
        assert removed == 1
        assert sample_deck.total_cards == 1

    def test_deduplicate_different_cards(self, sample_deck):
        from flashcards_generator.domain.entities import Flashcard

        card1 = Flashcard(front="Python is a language", back="Yes")
        card2 = Flashcard(front="Java is a language", back="Yes")
        card3 = Flashcard(front="C++ is a language", back="Yes")
        sample_deck.add_flashcard(card1)
        sample_deck.add_flashcard(card2)
        sample_deck.add_flashcard(card3)

        removed = sample_deck.deduplicate()
        assert removed == 0
        assert sample_deck.total_cards == 3
