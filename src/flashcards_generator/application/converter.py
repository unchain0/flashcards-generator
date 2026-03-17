"""Converter for creating cloze deletion flashcards."""

from __future__ import annotations

import re
from typing import ClassVar

from flashcards_generator.application.math_processor import convert_to_anki_math_format
from flashcards_generator.domain.entities import Flashcard


class ClozeConverter:
    """Convert flashcards to cloze deletion format."""

    # Pre-compiled regex patterns for performance
    CLOZE_PATTERN: ClassVar[re.Pattern] = re.compile(r"\{\{c\d+::(.+?)\}\}")
    WHITESPACE_PATTERN: ClassVar[re.Pattern] = re.compile(r"\s+")
    ELLIPSIS_PATTERN: ClassVar[re.Pattern] = re.compile(r"\.{3,}")
    RESPOSTA_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"\bResposta\s+[ée]/são\s*", flags=re.IGNORECASE
    )
    ANSWER_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"\bAnswer\s+is/are\s*", flags=re.IGNORECASE
    )
    QUESTION_CLEANUP_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"^(Qual é|Qual|What is|What|Which is|Which)\s*(o|a|the)?\s*",
        flags=re.IGNORECASE,
    )
    SENTENCE_SPLIT_PATTERN: ClassVar[re.Pattern] = re.compile(r"(?<=[.!?])\s+")
    WORD_CLEAN_PATTERN: ClassVar[re.Pattern] = re.compile(r"[,;:!?]$")

    # Patterns for extracting important content
    IMPORTANT_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"([A-Z][a-z]+(?:\s+[a-z]+){0,4}\s+(?:é|são|is|are)\s+[^(,|.)]+)"),
        re.compile(r"((?:é|são|is|are)\s+[^(,|.)]+)"),
        re.compile(r"([^(,|.)]{10,50})"),
    ]

    KEYWORDS: ClassVar[list[str]] = [
        "definido como",
        "caracterizado por",
        "representa",
        "refere-se a",
        "denomina-se",
        "conhecido como",
        "principais",
        "função",
        "objetivo",
        "finalidade",
        "causa",
        "efeito",
        "consequência",
        "resultado",
        "processo",
        "mecanismo",
        "método",
        "técnica",
        "estrutura",
        "composição",
        "formado por",
        "localizado",
        "encontra-se",
        "situa-se",
        "responsável",
        "atua",
        "funciona",
        "diferença",
        "semelhança",
        "característica",
        "exemplo",
        "defined as",
        "characterized by",
        "represents",
        "refers to",
        "known as",
        "called",
        "main",
        "primary",
        "major",
        "function",
        "purpose",
        "goal",
        "cause",
        "effect",
        "result",
        "process",
        "mechanism",
        "method",
        "structure",
        "composed of",
        "located",
        "responsible",
        "acts",
        "works",
        "difference",
        "similarity",
        "feature",
    ]

    TRIVIAL_WORDS: ClassVar[set[str]] = {
        "é",
        "são",
        "foi",
        "foram",
        "será",
        "serão",
        "is",
        "are",
        "was",
        "were",
        "will be",
        "o",
        "a",
        "os",
        "as",
        "the",
        "um",
        "uma",
        "uns",
        "umas",
        "an",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "of",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "in",
        "on",
        "at",
        "e",
        "and",
        "ou",
        "or",
        "mas",
        "but",
        "que",
        "that",
        "which",
        "who",
        "com",
        "with",
        "sem",
        "without",
        "por",
        "para",
        "by",
        "for",
        "to",
        "se",
        "if",
        "whether",
        "como",
        "like",
        "mais",
        "maior",
        "more",
        "most",
        "menos",
        "menor",
        "less",
        "least",
        "muito",
        "pouco",
        "much",
        "many",
        "little",
        "few",
        "bem",
        "mal",
        "well",
        "badly",
        "já",
        "ainda",
        "yet",
        "still",
        "already",
        "também",
        "too",
        "also",
        "either",
        "só",
        "somente",
        "apenas",
        "only",
        "todos",
        "todas",
        "todo",
        "toda",
        "all",
        "every",
        "nenhum",
        "nenhuma",
        "none",
        "algum",
        "alguma",
        "alguns",
        "algumas",
        "some",
        "any",
        "esse",
        "essa",
        "esses",
        "essas",
        "this",
        "these",
        "those",
        "direita",
        "direito",
        "esquerda",
        "esquerdo",
        "right",
        "left",
    }

    def convert(self, flashcard: Flashcard) -> Flashcard | None:
        """Convert a flashcard to cloze deletion format."""
        question = self._clean(flashcard.front)
        answer = self._clean(flashcard.back)

        if "{{c" in question:
            front = convert_to_anki_math_format(question)
            if not self._is_quality_valid(front):
                return None
            return Flashcard(front=front, back=answer, tags=flashcard.tags)

        cloze_text = self._create_cloze(question, answer, 1)

        if not self._is_quality_valid(cloze_text):
            return None

        return Flashcard(front=cloze_text, back=flashcard.back, tags=flashcard.tags)

    def _is_quality_valid(self, cloze_text: str) -> bool:
        if not cloze_text or len(cloze_text) < 10:
            return False

        matches = self.CLOZE_PATTERN.findall(cloze_text)

        for match in matches:
            clean_content = match.strip().lower()
            if clean_content in self.TRIVIAL_WORDS:
                return False
            words = clean_content.split()
            if words and all(w.lower() in self.TRIVIAL_WORDS for w in words):
                return False

        return True

    def _clean(self, text: str) -> str:
        text = self.WHITESPACE_PATTERN.sub(" ", text)
        text = text.strip()
        text = self.ELLIPSIS_PATTERN.sub("...", text)
        text = self.RESPOSTA_PATTERN.sub("", text)
        text = self.ANSWER_PATTERN.sub("", text)
        return text

    def _create_cloze(self, question: str, answer: str, card_num: int) -> str:
        answer_words = answer.split()

        if len(answer_words) <= 3:
            return self._create_simple_cloze(question, answer, card_num)

        return self._create_complex_cloze(answer, card_num)

    def _create_simple_cloze(self, question: str, answer: str, card_num: int) -> str:
        if answer.strip().lower() in self.TRIVIAL_WORDS:
            return ""

        if any(w in question.lower() for w in ["qual", "what", "which"]):
            cleaned_q = self.QUESTION_CLEANUP_PATTERN.sub("", question)
            cleaned_q = cleaned_q.rstrip("?").strip()
            if cleaned_q:
                return f"{cleaned_q} {{{{c{card_num}::{answer}}}}}"

        return f"{question} {{{{c{card_num}::{answer}}}}}"

    def _create_complex_cloze(self, answer: str, card_num: int) -> str:
        sentences = self.SENTENCE_SPLIT_PATTERN.split(answer)

        if len(sentences) == 1:
            return self._process_sentence(answer, card_num)

        clozes = []
        cloze_counter = 0

        for sentence in sentences[:3]:
            if len(sentence.strip()) > 10:
                cloze_counter += 1
                important = self._extract_important(sentence)
                if important and important.strip().lower() not in self.TRIVIAL_WORDS:
                    cloze_sentence = sentence.replace(
                        important,
                        f"{{{{c{card_num + cloze_counter - 1}::{important}}}}}",
                        1,
                    )
                    clozes.append(cloze_sentence)
                else:
                    clozes.append(sentence)

        return " ".join(clozes)

    def _create_word_cloze(self, words: list[str], card_num: int) -> str:
        idx = self._find_important_index(words)
        if idx >= 0:
            word = words[idx]
            if word.strip().lower() not in self.TRIVIAL_WORDS:
                words[idx] = f"{{{{c{card_num}::{word}}}}}"
                return " ".join(words)
        return " ".join(words)

    def _is_keyword(self, word: str) -> bool:
        clean = self.WORD_CLEAN_PATTERN.sub("", word.lower())
        return clean in self.KEYWORDS and clean not in self.TRIVIAL_WORDS

    def _create_multi_cloze(self, words: list[str], card_num: int) -> str:
        cloze_parts = []
        cloze_counter = 0

        for word in words:
            if self._is_keyword(word) and cloze_counter < 3:
                cloze_counter += 1
                cloze_parts.append(f"{{{{c{card_num + cloze_counter - 1}::{word}}}}}")
            else:
                cloze_parts.append(word)

        return " ".join(cloze_parts)

    def _process_sentence(self, sentence: str, card_num: int) -> str:
        words = sentence.split()

        if len(words) <= 5:
            return self._create_word_cloze(words, card_num)

        return self._create_multi_cloze(words, card_num)

    def _extract_important(self, sentence: str) -> str:
        for pattern in self.IMPORTANT_PATTERNS:
            match = pattern.search(sentence)
            if match:
                candidate = match.group(1).strip()
                words = candidate.split()
                if words and not all(w.lower() in self.TRIVIAL_WORDS for w in words):
                    return candidate

        return sentence[:30].strip()

    def _find_important_index(self, words: list[str]) -> int:
        for i, word in enumerate(words):
            clean = self.WORD_CLEAN_PATTERN.sub("", word.lower())
            if clean not in self.TRIVIAL_WORDS and (word[0].isupper() or i > 0):
                return i
        return len(words) // 2
