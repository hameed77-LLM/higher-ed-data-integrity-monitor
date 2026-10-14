"""Reader/writer for Pick-style MultiValue records (the UniData format under Ellucian Colleague).

A MultiValue record is a single delimited string:
    ID <AM> attr1 <AM> attr2 ...
Any attribute may hold several values separated by a value mark (VM), and a value
may hold sub-values separated by a sub-value mark (SVM). Related multivalued
attributes (for example TERMS and TERM.STATUS) form an "association": the Nth value
of one belongs with the Nth value of the other.

Dates are stored in Pick "internal" format: the number of days since 31 Dec 1967.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import zip_longest
from pathlib import Path

AM = chr(254)   # attribute mark
VM = chr(253)   # value mark
SVM = chr(252)  # sub-value mark

PICK_EPOCH = date(1967, 12, 31)
ENCODING = "latin-1"  # one byte per mark, same as a raw UniData export


def to_internal_date(d: date | None) -> str:
    return "" if d is None else str((d - PICK_EPOCH).days)


def from_internal_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.lstrip("-").isdigit():
        raise ValueError(f"not an internal date: {raw!r}")
    return PICK_EPOCH + timedelta(days=int(raw))


def encode_record(record_id: str, attributes: list[list[str] | str]) -> str:
    parts = [record_id]
    for attr in attributes:
        parts.append(VM.join(attr) if isinstance(attr, list) else attr)
    return AM.join(parts)


def decode_record(line: str) -> tuple[str, list[list[str]]]:
    """Return (record_id, attributes). Attribute N is at index N-1; each is a list of values."""
    fields = line.rstrip("\n").split(AM)
    record_id, attrs = fields[0], fields[1:]
    return record_id, [a.split(VM) if a else [] for a in attrs]


# Dictionary for the demo STUDENTS file: attribute number -> field name.
STUDENTS_DICT = {
    1: "LAST.NAME",
    2: "FIRST.NAME",
    3: "BIRTH.DATE",    # internal date
    4: "EMAIL",         # multivalued: personal, school
    5: "SLATE.REF",
    6: "TERMS",         # multivalued, associated with TERM.STATUS
    7: "TERM.STATUS",   # multivalued, associated with TERMS
    8: "PROGRAM",
}


@dataclass
class ParsedStudents:
    students: list[dict]
    terms: list[dict]
    parse_errors: list[dict]


def _single(attrs: list[list[str]], n: int) -> str | None:
    vals = attrs[n - 1] if len(attrs) >= n else []
    return vals[0].strip() or None if vals else None


def parse_students_file(path: str | Path) -> ParsedStudents:
    """Flatten a STUDENTS MultiValue file into relational rows.

    students: one row per record, with the counts of each associated multivalue so
              association breaks (TERMS vs TERM.STATUS) can be caught in SQL.
    terms:    one row per position in the TERMS/TERM.STATUS association.
    """
    students, terms, errors = [], [], []
    text = Path(path).read_text(encoding=ENCODING)
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        student_id, attrs = decode_record(line)
        try:
            birth = from_internal_date(_single(attrs, 3) or "")
        except ValueError as exc:
            errors.append({"record_id": student_id, "line": line_no, "error": str(exc)})
            birth = None
        emails = attrs[3] if len(attrs) >= 4 else []
        term_vals = attrs[5] if len(attrs) >= 6 else []
        status_vals = attrs[6] if len(attrs) >= 7 else []
        students.append({
            "student_id": student_id,
            "last_name": _single(attrs, 1),
            "first_name": _single(attrs, 2),
            "birth_date": birth.isoformat() if birth else None,
            "personal_email": (emails[0].strip().lower() or None) if len(emails) > 0 else None,
            "school_email": (emails[1].strip().lower() or None) if len(emails) > 1 else None,
            "slate_ref": _single(attrs, 5),
            "program": _single(attrs, 8),
            "term_count": len(term_vals),
            "status_count": len(status_vals),
        })
        for pos, (term, status) in enumerate(zip_longest(term_vals, status_vals), start=1):
            terms.append({
                "student_id": student_id,
                "position": pos,
                "term": (term or "").strip() or None,
                "status": (status or "").strip() or None,
            })
    return ParsedStudents(students, terms, errors)
