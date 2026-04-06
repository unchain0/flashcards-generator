"""Process and preserve LaTeX/math in flashcards."""

from __future__ import annotations

import re
from typing import ClassVar


class MathProcessor:
    """Process and preserve LaTeX/math in flashcards."""

    MATH_INLINE_PATTERNS: ClassVar[list[tuple[str, str]]] = [
        (r"\\\((.+?)\\\)", "paren"),
        (r"\$([^$]+)\$", "dollar"),
    ]

    MATH_DISPLAY_PATTERNS: ClassVar[list[tuple[str, str]]] = [
        (r"\$\$([^$]+)\$\$", "ddollar"),  # $$...$$
        (r"\\\[(.+?)\\\]", "bracket"),  # \[...\]
    ]

    PLACEHOLDER_PREFIX: ClassVar[str] = "MATHPLACEHOLDER"

    def __init__(self) -> None:
        self.math_storage: dict[str, str] = {}
        self.counter = 0

    def extract_and_replace(self, text: str) -> str:
        """Extrai math do texto e substitui por placeholders."""
        self.math_storage = {}
        self.counter = 0

        # Primeiro extrair math display (maior prioridade)
        for pattern, _ in self.MATH_DISPLAY_PATTERNS:
            text = self._replace_math(text, pattern, is_display=True)

        # Depois extrair math inline
        for pattern, _ in self.MATH_INLINE_PATTERNS:
            text = self._replace_math(text, pattern, is_display=False)

        return text

    def _replace_math(self, text: str, pattern: str, is_display: bool) -> str:
        """Substitui math por placeholder."""

        def replace_match(match: re.Match) -> str:
            self.counter += 1
            placeholder = f"{self.PLACEHOLDER_PREFIX}{self.counter:04d}"
            math_content = match.group(0)
            self.math_storage[placeholder] = math_content
            return placeholder

        return re.sub(pattern, replace_match, text)

    def restore_math(self, text: str) -> str:
        """Restaura o math nos placeholders."""
        # Ordenar placeholders do maior para o menor para evitar substituições parciais
        for placeholder in sorted(self.math_storage.keys(), reverse=True):
            math_content = self.math_storage[placeholder]
            text = text.replace(placeholder, math_content)
        return text

    def has_math(self, text: str) -> bool:
        """Verifica se o texto contém math."""
        for pattern, _ in self.MATH_INLINE_PATTERNS + self.MATH_DISPLAY_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def process_for_cloze(self, text: str, cloze_marker: str) -> str:
        """Process text with math to create cloze.

        Ensures math doesn't break the cloze format.
        """
        # Extrair math
        text_with_placeholders = self.extract_and_replace(text)

        # Aplicar cloze no texto com placeholders
        # O cloze_marker já deve vir no formato {{cN::texto}}

        # Restaurar math
        result = self.restore_math(text_with_placeholders)

        return result


def extract_math_segments(text: str) -> list[tuple[str, bool]]:
    """Extract text and math segments.

    Returns list of (segment, is_math).
    """
    segments = []

    # Padrão combinado para math inline e display
    math_pattern = r"(\$\$[^$]+\$\$|\$[^$]+\$|\\\[.+?\\\]|\\\(.+?\\\))"

    last_end = 0
    for match in re.finditer(math_pattern, text):
        # Texto antes do math
        if match.start() > last_end:
            segments.append((text[last_end : match.start()], False))
        # O math
        segments.append((match.group(0), True))
        last_end = match.end()

    # Texto restante
    if last_end < len(text):
        segments.append((text[last_end:], False))

    if not segments:
        segments.append((text, False))

    return segments


def convert_to_anki_math_format(text: str) -> str:
    """Convert dollar math notation to Anki LaTeX format."""
    text = re.sub(r"\$\$([^$]+)\$\$", r"\\[\1\\]", text)
    text = re.sub(r"\$([^$]+)\$", r"\\(\1\\)", text)
    return text


def create_cloze_with_math(text: str, answer: str, card_num: int) -> str:
    """Create cloze deletion with math support."""
    processor = MathProcessor()

    if processor.has_math(answer):
        answer_with_placeholders = processor.extract_and_replace(answer)
        cloze_content = f"{{{{c{card_num}::{answer_with_placeholders}}}}}"
        result = processor.restore_math(cloze_content)
        return convert_to_anki_math_format(result)
    else:
        return f"{{{{c{card_num}::{answer}}}}}"
