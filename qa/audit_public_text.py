"""Flag potentially sensitive public rationale/error text without revealing matches.

Heuristics are a review aid, never proof of anonymization. No text is rewritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from qa.audit_submission import strict_json


PATTERNS = {
    "email": re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE),
    "url": re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S+"),
    "iban": re.compile(r"(?i)(?<![A-Z0-9])[A-Z]{2}[ ]?\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Z0-9])"),
    "credential_marker": re.compile(
        r"(?i)(?:\b(?:api[_ -]?key|access[_ -]?token|secret|password|пароль|токен|секрет(?:ный)?\s+ключ)"
        r"\s*[:=]\s*[^\s,;]+|[?&](?:sig|signature|token|key|code)=[^\s&#]+)"),
    "credential_shape": re.compile(
        r"(?:\bAIza[0-9A-Za-z_-]{30,}\b|\bsk-[0-9A-Za-z_-]{20,}\b|"
        r"\bgh[pousr]_[0-9A-Za-z]{20,}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
        r"\beyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\b)"),
}
NUMBERS = re.compile(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)")


def _luhn(digits):
    numbers = [int(char) for char in digits]
    return sum(number if index % 2 != len(numbers) % 2 else number * 2 - (9 if number > 4 else 0)
               for index, number in enumerate(numbers)) % 10 == 0


def _flags(text):
    flags = {name for name, pattern in PATTERNS.items() if pattern.search(text)}
    for match in NUMBERS.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 10 <= len(digits) <= 15:
            flags.add("phone_or_long_identifier")
        if 13 <= len(digits) <= 19 and _luhn(digits):
            flags.add("possible_payment_card")
    return sorted(flags)


def scan_public_text(payload):
    """Return paths/rule codes only. Official format validation is a separate step."""
    report = {"scan_completed": False, "scanned_fields": 0, "flags": [],
              "limitation": "Heuristics require manual review and cannot prove absence of personal data or credentials."}
    try:
        payload = strict_json(payload)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        report["input_error"] = "invalid_utf8_json"
        return report
    if type(payload) is not dict or type(payload.get("results")) is not list:
        report["input_error"] = "invalid_results"
        return report
    for index, row in enumerate(payload["results"]):
        if type(row) is not dict:
            report["input_error"] = "invalid_result"
            return report
        fields = []
        if "error" in row:
            fields.append((f"results[{index}].error", row["error"]))
        if type(row.get("criteria")) is list:
            for ci, criterion in enumerate(row["criteria"]):
                if type(criterion) is dict and "rationale" in criterion:
                    fields.append((f"results[{index}].criteria[{ci}].rationale", criterion["rationale"]))
        for path, value in fields:
            if type(value) is not str:
                report["input_error"] = "invalid_public_text"
                return report
            report["scanned_fields"] += 1
            for rule in _flags(value):
                report["flags"].append({"path": path, "rule": rule})
    report["scan_completed"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.results.resolve() == args.report.resolve():
        parser.exit(2, "Report must not overwrite the input.\n")
    try:
        blob = args.results.read_bytes()
    except OSError:
        parser.exit(2, "Input unavailable; no content printed.\n")
    report = scan_public_text(blob)
    report["results_sha256"] = hashlib.sha256(blob).hexdigest()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scan_completed": report["scan_completed"], "scanned_fields": report["scanned_fields"],
                      "flag_count": len(report["flags"]), "results_sha256": report["results_sha256"]}))
    raise SystemExit(2 if not report["scan_completed"] else 1 if report["flags"] else 0)


if __name__ == "__main__":
    main()
