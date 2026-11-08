"""Narrow public-text projection of names explicitly introduced in the source.

This is not a general personal-data detector. Discovery mirrors the explicit-name
grammar documented in docs/official-export.md and independently audited by QA.
Only a new string is returned; source and saved model responses are never changed.
"""
from __future__ import annotations

from collections.abc import Iterable
import re


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_NAME_WORD = r"[A-ZА-ЯЁӘІҢҒҮҰҚӨҺ][a-zа-яёәіңғүұқөһ]+(?:[-'][A-ZА-ЯЁӘІҢҒҮҰҚӨҺ]?[a-zа-яёәіңғүұқөһ]+)?"
_NAME = re.compile(
    r"(?i:\b(?:меня\s+зовут|мо[её]\s+имя|my\s+name\s+is|I\s+am|I['’]m|"
    r"менің\s+атым|есімім|аты-жөнім|ФИО|full\s+name))\s*[:=—-]?\s*(?P<value>"
    + _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){0,2})"
)
_NOT_NAMES = frozenset(word.casefold() for word in (
    "Unable", "Not", "Trying", "Looking", "New", "Interested", "Having", "Hello", "Здравствуйте",
))
_MARKER = "[имя скрыто]"


def redact_source_names(text: str, source_texts: Iterable[str]) -> str:
    """Replace full token sequences of explicit source names, only within text.

    Source texts must belong to this conversation, including every role. Name
    introductions require title-case name words; output matching uses Unicode
    casefold and whole letter/digit tokens. Names with more tokens win overlaps;
    punctuation outside the replaced spans is preserved exactly.
    """
    names = set()
    for source in source_texts:
        for match in _NAME.finditer(source):
            value = match.group("value").strip()
            if value.split()[0].casefold() not in _NOT_NAMES:
                names.add(tuple(token.group().casefold() for token in _WORD.finditer(value)))
    matches = list(_WORD.finditer(text))
    tokens = [match.group().casefold() for match in matches]
    occupied = set()
    spans = []
    for name in sorted(names, key=lambda item: (-len(item), -len(" ".join(item)), item)):
        size = len(name)
        for start in range(len(tokens) - size + 1):
            end = start + size
            if tuple(tokens[start:end]) == name and not occupied.intersection(range(start, end)):
                spans.append((matches[start].start(), matches[end - 1].end()))
                occupied.update(range(start, end))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + _MARKER + text[end:]
    return text
