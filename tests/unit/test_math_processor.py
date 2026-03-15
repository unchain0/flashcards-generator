from flashcards_generator.application.math_processor import (
    MathProcessor,
    convert_to_anki_math_format,
    create_cloze_with_math,
    extract_math_segments,
)


class TestMathProcessor:
    def test_detect_inline_math_dollar(self):
        processor = MathProcessor()
        text = "A fórmula $E=mc^2$ é famosa"
        assert processor.has_math(text) is True

    def test_detect_inline_math_paren(self):
        processor = MathProcessor()
        text = "A fórmula \\(E=mc^2\\) é famosa"
        assert processor.has_math(text) is True

    def test_detect_display_math_ddollar(self):
        processor = MathProcessor()
        text = "A equação $$E=mc^2$$ é famosa"
        assert processor.has_math(text) is True

    def test_detect_display_math_bracket(self):
        processor = MathProcessor()
        text = "A equação \\[E=mc^2\\] é famosa"
        assert processor.has_math(text) is True

    def test_no_math(self):
        processor = MathProcessor()
        text = "Texto normal sem matemática"
        assert processor.has_math(text) is False

    def test_extract_and_replace_inline(self):
        processor = MathProcessor()
        text = "A fórmula $E=mc^2$ é famosa"
        result = processor.extract_and_replace(text)

        assert "MATHPLACEHOLDER" in result
        assert "$E=mc^2$" not in result
        assert len(processor.math_storage) == 1

    def test_extract_and_replace_multiple(self):
        processor = MathProcessor()
        text = "As fórmulas $E=mc^2$ e $F=ma$ são famosas"
        result = processor.extract_and_replace(text)

        assert result.count("MATHPLACEHOLDER") == 2
        assert len(processor.math_storage) == 2

    def test_restore_math(self):
        processor = MathProcessor()
        text = "A fórmula $E=mc^2$ é famosa"
        text_with_placeholders = processor.extract_and_replace(text)
        result = processor.restore_math(text_with_placeholders)

        assert "$E=mc^2$" in result
        assert "MATHPLACEHOLDER" not in result

    def test_extract_and_restore_display_math(self):
        processor = MathProcessor()
        text = "A equação $$\\int_0^1 x dx$$ é importante"
        text_with_placeholders = processor.extract_and_replace(text)
        result = processor.restore_math(text_with_placeholders)

        assert "$$\\int_0^1 x dx$$" in result

    def test_process_for_cloze(self):
        processor = MathProcessor()
        text = "A fórmula $E=mc^2$ é de Einstein"
        result = processor.process_for_cloze(text, "{{c1::teste}}")

        # O math deve ser preservado
        assert "$E=mc^2$" in result


class TestExtractMathSegments:
    def test_text_with_inline_math(self):
        text = "A fórmula $E=mc^2$ é famosa"
        segments = extract_math_segments(text)

        assert len(segments) == 3
        assert segments[0] == ("A fórmula ", False)
        assert segments[1] == ("$E=mc^2$", True)
        assert segments[2] == (" é famosa", False)

    def test_only_text(self):
        text = "Texto sem math"
        segments = extract_math_segments(text)

        assert len(segments) == 1
        assert segments[0] == ("Texto sem math", False)

    def test_multiple_math(self):
        text = "$a$ e $b$ são variáveis"
        segments = extract_math_segments(text)

        assert len(segments) == 4
        assert segments[0] == ("$a$", True)
        assert segments[1] == (" e ", False)
        assert segments[2] == ("$b$", True)
        assert segments[3] == (" são variáveis", False)


class TestCreateClozeWithMath:
    def test_cloze_without_math(self):
        result = create_cloze_with_math("Qual a capital?", "Paris", 1)
        assert "{{c1::Paris}}" in result

    def test_cloze_with_math_in_answer(self):
        result = create_cloze_with_math("Qual a fórmula?", "$E=mc^2$", 1)
        assert "{{c1::" in result
        assert "\\(E=mc^2\\)" in result
        assert "$E=mc^2$" not in result

    def test_cloze_preserves_math_delimiters(self):
        result = create_cloze_with_math("Qual a equação?", "$$\\sum_{i=1}^n i$$", 1)
        assert "\\[\\sum_{i=1}^n i\\]" in result


class TestConvertToAnkiMathFormat:
    def test_convert_inline_dollar_to_paren(self):
        text = "A fórmula $E=mc^2$ é famosa"
        result = convert_to_anki_math_format(text)
        assert "\\(E=mc^2\\)" in result
        assert "$E=mc^2$" not in result

    def test_convert_display_dollar_to_bracket(self):
        text = "A equação $$E=mc^2$$ é famosa"
        result = convert_to_anki_math_format(text)
        assert "\\[E=mc^2\\]" in result
        assert "$$E=mc^2$$" not in result

    def test_no_change_if_already_anki_format(self):
        text = "A fórmula \\(E=mc^2\\) já está no formato correto"
        result = convert_to_anki_math_format(text)
        assert "\\(E=mc^2\\)" in result
