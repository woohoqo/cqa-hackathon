"""Match explicit source identity details to public rationales, without outputting values.

This is a conservative review aid, not general named-entity detection or anonymization.
Long generic overlaps are counted but never promoted to personal-data findings.
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re

from qa.audit_submission import strict_json

WORD = re.compile(r"[^\W_]+", re.UNICODE)
NAME_WORD = r"[A-ZА-ЯЁӘІҢҒҮҰҚӨҺ][a-zа-яёәіңғүұқөһ]+(?:[-'][A-ZА-ЯЁӘІҢҒҮҰҚӨҺ]?[a-zа-яёәіңғүұқөһ]+)?"
NAME = re.compile(
    r"(?i:\b(?:меня\s+зовут|мо[её]\s+имя|my\s+name\s+is|I\s+am|I['’]m|"
    r"менің\s+атым|есімім|аты-жөнім|ФИО|full\s+name))\s*[:=—-]?\s*(?P<value>"
    + NAME_WORD + r"(?:\s+" + NAME_WORD + r"){0,2})"
)
IDENTIFIER = re.compile(
    r"(?i)\b(?:ИИН|IIN|ИНН|БИН|номер\s+(?:сч[её]та|карты|паспорта|документа)|"
    r"account\s*(?:number|id)|card\s*number|passport\s*(?:number|id)|"
    r"мой\s+логин|my\s+(?:login|username)|логин|login|username|шот\s+н[өо]мірі)"
    r"\s*(?:это|is)?\s*[:=№—-]?\s*(?P<value>[A-ZА-Я0-9_*][A-ZА-Я0-9_* -]{3,34})"
)
ADDRESS = re.compile(
    r"(?i)\b(?:мой\s+адрес|адрес\s+проживания|проживаю\s+по\s+адресу|home\s+address|"
    r"my\s+address|менің\s+мекенжайым)\s*(?:is)?\s*[:=—-]?\s*(?P<value>[^\n;!?]{8,100})"
)
NOT_NAMES = {"Unable", "Not", "Trying", "Looking", "New", "Interested", "Having", "Hello", "Здравствуйте"}


def _tokens(text):
    return [match.group().casefold() for match in WORD.finditer(text)]


def _details(text):
    details = []
    for match in NAME.finditer(text):
        value = match.group("value").strip()
        if value.split()[0] not in NOT_NAMES:
            details.append(("explicit_person_name", _tokens(value)))
    for match in IDENTIFIER.finditer(text):
        value = match.group("value").strip()
        # Label alone or generic words such as 'account number unknown' are not PII.
        parts = value.split()
        identifier = next((part for part in parts if sum(char.isdigit() for char in part) >= 4), None)
        if identifier:
            details.append(("explicit_account_or_identity_number", _tokens(identifier)))
        elif len(parts) == 1 and re.search(r"[_*]", value) and len(value) >= 6:
            details.append(("explicit_login", _tokens(value)))
    for match in ADDRESS.finditer(text):
        value = match.group("value").strip()
        if any(char.isdigit() for char in value) and len(_tokens(value)) >= 3:
            details.append(("explicit_home_address", _tokens(value)))
    return [(kind, tokens) for kind, tokens in details if tokens]


def _contains(haystack, needle):
    return any(haystack[index:index + len(needle)] == needle for index in range(len(haystack) - len(needle) + 1))


def review_source_privacy(payload, source):
    report = {"review_completed": False, "scanned_fields": 0, "source_identity_candidates": 0,
              "long_source_overlaps": 0, "flags": [],
              "limitation": "Explicit identity markers and exact normalized overlap only; no general NER or guarantee of anonymization."}
    try:
        payload, source = strict_json(payload), strict_json(source)
        rows = payload["results"]
        records = source["data"]["records"]
        if (type(rows) is not list or type(records) is not list
                or any(type(row) is not dict or type(row.get("conversation_id")) is not str for row in records)):
            raise ValueError()
        by_id = {row["conversation_id"]: row for row in records}
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
        report["input_error"] = "invalid_review_inputs"
        return report
    for ri, row in enumerate(rows):
        if (type(row) is not dict or type(row.get("conversation_id")) is not str
                or row["conversation_id"] not in by_id):
            report["input_error"] = "result_outside_source"
            return report
        record = by_id[row["conversation_id"]]
        transcript = record.get("transcript")
        if type(transcript) is not list or any(type(m) is not dict or type(m.get("text")) is not str for m in transcript):
            report["input_error"] = "invalid_source_transcript"
            return report
        source_texts = [(turn, _tokens(message["text"]), _details(message["text"]),
                         message.get("speaker") if message.get("speaker") in ("client", "operator", "bot", "system") else "other")
                        for turn, message in enumerate(transcript, 1)]
        report["source_identity_candidates"] += sum(len(details) for _, _, details, _ in source_texts)
        fields = []
        if "error" in row:
            fields.append((f"results[{ri}].error", row["error"]))
        if type(row.get("criteria")) is list:
            fields.extend((f"results[{ri}].criteria[{ci}].rationale", item.get("rationale"))
                          for ci, item in enumerate(row["criteria"]) if type(item) is dict)
        for path, text in fields:
            if type(text) is not str:
                report["input_error"] = "invalid_public_text"
                return report
            report["scanned_fields"] += 1
            tokens = _tokens(text)
            for turn, source_tokens, details, role in source_texts:
                match = SequenceMatcher(None, source_tokens, tokens, autojunk=False).find_longest_match()
                long = match.size >= 6 and len(" ".join(tokens[match.b:match.b + match.size])) >= 30
                if long:
                    report["long_source_overlaps"] += 1
                for kind, detail in details:
                    if _contains(tokens, detail):
                        finding = {"path": path, "source_turn": turn, "kind": kind,
                                   "source_role": role, "long_verbatim_overlap": long}
                        if finding not in report["flags"]:
                            report["flags"].append(finding)
    report["review_completed"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.resolve() in {args.results.resolve(), args.source.resolve()}:
        parser.exit(2, "Report must not overwrite inputs.\n")
    try:
        blobs = [path.read_bytes() for path in (args.results, args.source)]
    except OSError:
        parser.exit(2, "Input unavailable; no content printed.\n")
    report = review_source_privacy(*blobs)
    report["file_sha256"] = dict(zip(("results", "source"), (hashlib.sha256(blob).hexdigest() for blob in blobs)))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"review_completed": report["review_completed"], "scanned_fields": report["scanned_fields"],
                      "source_identity_candidates": report["source_identity_candidates"],
                      "long_source_overlaps": report["long_source_overlaps"], "flag_count": len(report["flags"]),
                      "results_sha256": report["file_sha256"]["results"]}))
    raise SystemExit(2 if not report["review_completed"] else 1 if report["flags"] else 0)


if __name__ == "__main__":
    main()
