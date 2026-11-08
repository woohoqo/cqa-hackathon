"""Synthetic names and source snippets only; no organizer transcripts."""
import pytest

from backend.app.export_privacy import redact_source_names


@pytest.mark.parametrize("introduction,name", [
    ("Меня зовут Лира.", "Лира"), ("МОЁ ИМЯ: Лира.", "Лира"),
    ("Мое имя — Лира.", "Лира"), ("Менің атым Қиял.", "Қиял"),
    ("Есімім: Қиял.", "Қиял"), ("Аты-жөнім = Қиял Сынақ.", "Қиял Сынақ"),
    ("My name is Elara.", "Elara"), ("I am Elara.", "Elara"),
    ("I'm Elara.", "Elara"), ("I’m Elara.", "Elara"),
    ("ФИО: Лира Пример.", "Лира Пример"), ("Full name: Elara Example.", "Elara Example"),
])
def test_explicit_multilingual_introduction_and_output_casefold(introduction, name):
    assert redact_source_names(f"({name.upper()}), {name.casefold()}!", [introduction]) == (
        "([имя скрыто]), [имя скрыто]!"
    )


@pytest.mark.parametrize("not_name", [
    "Unable", "Not", "Trying", "Looking", "New", "Interested", "Having", "Hello", "Здравствуйте",
])
def test_generic_self_description_is_not_a_name(not_name):
    text = f"{not_name} appears in a synthetic explanation."
    assert redact_source_names(text, [f"I am {not_name}."]) == text


def test_longest_name_wins_over_shorter_prefix_and_overlapping_later_start():
    sources = ["My name is Elara.", "My name is Elara Example.",
               "My name is Example Longer Name."]
    assert redact_source_names("Elara Example; Elara; Elara Example Longer Name.", sources) == (
        "[имя скрыто]; [имя скрыто]; [имя скрыто] [имя скрыто]."
    )


def test_whole_tokens_punctuation_and_spacing_without_substring_replacement():
    sources = ["My name is Elara Example.", "Меня зовут Лира."]
    text = "  «ELARA,\tExample» / ЛИРА! Лирам; Elara Examples; 7Лира; Elara.\r\n"
    assert redact_source_names(text, sources) == (
        "  «[имя скрыто]» / [имя скрыто]! Лирам; Elara Examples; 7Лира; Elara.\r\n"
    )


def test_hyphen_apostrophe_name_and_tokenized_variants():
    sources = ["I am Elara-Quill.", "My name is De'Elara."]
    assert redact_source_names("Elara-Quill, ELARA QUILL; De’Elara!", sources) == (
        "[имя скрыто], [имя скрыто]; [имя скрыто]!"
    )


def test_scope_is_explicit_full_names_in_given_source_not_general_ner():
    text = "Elara; Лира; Қиял; customer42; 123456789012."
    assert redact_source_names(text, ["Elara said hello.", "Operator: Лира", "Қиял answered."]) == text
    assert redact_source_names(text, []) == text
    # Lowercase source mentions and first-name fragments of full names are not inferred.
    assert redact_source_names("Elara.", ["My name is elara.", "My name is Elara Example."]) == "Elara."


def test_source_inputs_unchanged_and_output_deterministic_with_duplicate_sources():
    sources = ["My name is Elara Example.", "My name is Elara."]
    before = sources[:]
    text = "Elara Example met Elara."
    expected = "[имя скрыто] met [имя скрыто]."
    assert redact_source_names(text, sources) == expected
    assert redact_source_names(text, iter(reversed(sources + sources))) == expected
    assert sources == before and text == "Elara Example met Elara."
