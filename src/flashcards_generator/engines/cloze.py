"""Converter for creating cloze deletion flashcards."""

from __future__ import annotations

import re
from typing import ClassVar

from flashcards_generator.domain_models.entities import Flashcard
from flashcards_generator.engines.math import (
    convert_to_anki_math_format,
)


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
        re.compile(
            r"([A-Z][a-z]+(?:\s+[a-z]+){0,4}\s+(?:é|são|is|are)\s+[^(,|.)]+)"
        ),
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

    def convert(
        self, flashcard: Flashcard, *, single_cloze: bool = False
    ) -> Flashcard | None:
        """Convert a flashcard to cloze deletion format."""
        question = self._clean(flashcard.front)
        answer = self._clean(flashcard.back)

        if "{{c" in question:
            return self._convert_existing_cloze(
                flashcard, question, answer, single_cloze
            )

        cloze_text = self._create_cloze(question, answer, 1)
        if single_cloze:
            cloze_text = self._limit_to_single_cloze(cloze_text)

        if not self._is_quality_valid(cloze_text):
            return None

        return Flashcard(
            front=cloze_text, back=flashcard.back, tags=flashcard.tags
        )

    def _convert_existing_cloze(
        self,
        flashcard: Flashcard,
        question: str,
        answer: str,
        single_cloze: bool,
    ) -> Flashcard | None:
        if single_cloze:
            question = self._limit_to_single_cloze(question)
        front = convert_to_anki_math_format(question)
        if not self._is_quality_valid(front):
            return None
        return Flashcard(front=front, back=answer, tags=flashcard.tags)

    def _limit_to_single_cloze(self, text: str) -> str:
        retained = False

        def replace(match: re.Match[str]) -> str:
            nonlocal retained
            content = match.group(1)
            answer = content.partition("::")[0]
            if not retained and self._has_meaningful_cloze_content(answer):
                retained = True
                return f"{{{{c1::{content}}}}}"
            return answer

        return self.CLOZE_PATTERN.sub(replace, text)

    def _is_quality_valid(self, cloze_text: str) -> bool:
        if len(cloze_text) < 10:
            return False

        matches = self.CLOZE_PATTERN.findall(cloze_text)
        return bool(matches) and all(
            self._has_meaningful_cloze_content(match) for match in matches
        )

    def _has_meaningful_cloze_content(self, content: str) -> bool:
        words = content.strip().lower().split()
        return bool(words) and any(
            word not in self.TRIVIAL_WORDS for word in words
        )

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

    def _create_simple_cloze(
        self, question: str, answer: str, card_num: int
    ) -> str:
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
            if len(sentence.strip()) <= 10:
                continue
            cloze_counter += 1
            clozes.append(
                self._create_sentence_cloze(
                    sentence, card_num + cloze_counter - 1
                )
            )

        return " ".join(clozes)

    def _create_sentence_cloze(self, sentence: str, card_num: int) -> str:
        important = self._extract_important(sentence)
        if important and important.strip().lower() not in self.TRIVIAL_WORDS:
            return sentence.replace(
                important, f"{{{{c{card_num}::{important}}}}}", 1
            )
        return sentence

    def _create_word_cloze(self, words: list[str], card_num: int) -> str:
        idx = self._find_important_index(words)
        word = words[idx]
        if word.strip().lower() not in self.TRIVIAL_WORDS:
            words[idx] = f"{{{{c{card_num}::{word}}}}}"
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
                cloze_parts.append(
                    f"{{{{c{card_num + cloze_counter - 1}::{word}}}}}"
                )
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
                if self._has_meaningful_cloze_content(candidate):
                    return candidate

        return sentence[:30].strip()

    def _find_important_index(self, words: list[str]) -> int:
        for i, word in enumerate(words):
            clean = self.WORD_CLEAN_PATTERN.sub("", word.lower())
            if clean not in self.TRIVIAL_WORDS and (
                word[0].isupper() or i > 0
            ):
                return i
        return len(words) // 2
