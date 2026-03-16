from flashcards_generator.domain.entities import Flashcard


class TestClozeConverter:
    def test_convert_simple_question(self, cloze_converter):
        card = Flashcard(front="Qual é a capital da França?", back="Paris")
        result = cloze_converter.convert(card)

        assert "{{c" in result.front
        assert "}}" in result.front
        assert "Paris" in result.front
        assert "::" in result.front

    def test_convert_complex_answer(self, cloze_converter):
        card = Flashcard(
            front="O que é fotossíntese?",
            back=(
                "Processo pelo qual as plantas convertem luz solar em energia química."
            ),
        )
        result = cloze_converter.convert(card)

        assert result.front != card.front
        assert "{{c" in result.front
        assert "}}" in result.front

    def test_clean_text(self, cloze_converter):
        dirty = "  Texto   com   espaços  "
        clean = cloze_converter._clean(dirty)
        assert clean == "Texto com espaços"

    def test_clean_text_multiple_dots(self, cloze_converter):
        text = "Texto...com....pontos..."
        clean = cloze_converter._clean(text)
        assert clean == "Texto...com...pontos..."

    def test_create_simple_cloze_without_which(self, cloze_converter):
        card = Flashcard(
            front="Defina fotossíntese", back="Processo de conversão de luz"
        )
        result = cloze_converter.convert(card)
        assert "{{c" in result.front
        assert "}}" in result.front
        assert "Processo" in result.front

    def test_create_simple_cloze_with_what_is(self, cloze_converter):
        card = Flashcard(front="What is the capital of Italy?", back="Rome")
        result = cloze_converter.convert(card)
        assert "capital of Italy" in result.front
        assert "{{c" in result.front
        assert "}}" in result.front

    def test_create_complex_cloze_multiple_sentences(self, cloze_converter):
        card = Flashcard(
            front="Explique a fotossíntese",
            back=(
                "A fotossíntese é um processo biológico. "
                "As plantas convertem luz em energia. Isso ocorre nas folhas."
            ),
        )
        result = cloze_converter.convert(card)
        assert result.front != card.back

    def test_process_sentence_short(self, cloze_converter):
        result = cloze_converter._process_sentence("Célula é a unidade", 1)
        assert "{{c" in result
        assert "}}" in result

    def test_process_sentence_long_with_keywords(self, cloze_converter):
        sentence = (
            "O processo de fotossíntese é caracterizado pela "
            "conversão de energia luminosa em energia química"
        )
        result = cloze_converter._process_sentence(sentence, 1)
        assert "{{c" in result
        assert "}}" in result
        assert (
            cloze_converter.KEYWORDS[0] in result.lower()
            or "processo" in result.lower()
        )

    def test_process_sentence_no_keywords(self, cloze_converter):
        sentence = "abcdef ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdef"
        result = cloze_converter._process_sentence(sentence, 1)
        assert "{{c" in result  # A palavra maiúscula é encontrada
        assert "}}" in result

    def test_extract_important_first_pattern(self, cloze_converter):
        sentence = "A Mitocondria é a organela responsavel pela respiracao celular"
        result = cloze_converter._extract_important(sentence)
        assert "Mitocondria" in result

    def test_extract_important_second_pattern(self, cloze_converter):
        sentence = "é um processo importante"
        result = cloze_converter._extract_important(sentence)
        assert "é" in result

    def test_extract_important_third_pattern(self, cloze_converter):
        sentence = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        result = cloze_converter._extract_important(sentence)
        assert len(result) <= 50

    def test_find_important_index_with_uppercase(self, cloze_converter):
        words = ["o", "a", "Mitocôndria", "é", "importante"]
        result = cloze_converter._find_important_index(words)
        assert result == 2

    def test_find_important_index_all_articles(self, cloze_converter):
        words = ["o", "a", "os", "as"]
        result = cloze_converter._find_important_index(words)
        assert result == 2  # len(words) // 2

    def test_find_important_index_with_articles(self, cloze_converter):
        words = ["o", "a", "importante"]
        result = cloze_converter._find_important_index(words)
        assert result == 2

    def test_convert_already_has_cloze(self, cloze_converter):
        card = Flashcard(front="Text with {{c1::cloze}} already", back="answer")
        result = cloze_converter.convert(card)
        assert result is not None
        assert "{{c" in result.front

    def test_convert_invalid_quality_short_text(self, cloze_converter):
        card = Flashcard(front="What?", back="X")
        result = cloze_converter.convert(card)
        assert result is not None

    def test_convert_trivial_answer(self, cloze_converter):
        card = Flashcard(front="What is this?", back="a")
        result = cloze_converter.convert(card)
        assert result is None

    def test_create_simple_cloze_trivial_word(self, cloze_converter):
        result = cloze_converter._create_simple_cloze("Qual é a?", "a", 1)
        assert result == ""

    def test_create_simple_cloze_with_what_no_article(self, cloze_converter):
        result = cloze_converter._create_simple_cloze("What is capital?", "Paris", 1)
        assert "{{c1::Paris}}" in result

    def test_create_complex_cloze_single_sentence(self, cloze_converter):
        result = cloze_converter._create_complex_cloze("A short sentence here.", 1)
        assert "{{c" in result

    def test_create_word_cloze_no_important_word(self, cloze_converter):
        words = ["a", "the", "is"]
        result = cloze_converter._create_word_cloze(words, 1)
        assert result == "a the is"

    def test_is_keyword_true(self, cloze_converter):
        result = cloze_converter._is_keyword("processo")
        assert result is True

    def test_is_keyword_false(self, cloze_converter):
        result = cloze_converter._is_keyword("xyz")
        assert result is False

    def test_extract_important_all_trivial(self, cloze_converter):
        sentence = "a e o são importantes"
        result = cloze_converter._extract_important(sentence)
        assert result == "são importantes"
