"""Pydantic schema for the Slate export: type checks and normalization on the way in.

Normalization (trimming, case, accepting MM/DD/YYYY dates) happens here so the SQL
rules downstream only deal with business problems, not formatting noise. Anything
that cannot be coerced is recorded as a schema error (rule R00) and nulled, rather
than crashing the load.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError, field_validator


def _blank_to_none(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


class Applicant(BaseModel):
    slate_ref: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    birth_date: Optional[date] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    entry_term: Optional[str] = None
    program_code: Optional[str] = None
    state: Optional[str] = None
    app_status: Optional[Literal["Submitted", "Admitted", "Denied", "Deposited"]] = None
    colleague_id: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def strip_blanks(cls, v):
        return _blank_to_none(v)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v):
        return v.lower() if v else v

    @field_validator("phone")
    @classmethod
    def digits_only(cls, v):
        if not v:
            return v
        digits = "".join(ch for ch in v if ch.isdigit())
        return digits[-10:] if len(digits) >= 10 else None

    @field_validator("entry_term", "program_code", "state")
    @classmethod
    def upper_codes(cls, v):
        return v.upper() if v else v

    @field_validator("birth_date", mode="before")
    @classmethod
    def parse_date(cls, v):
        v = _blank_to_none(v)
        if v is None or isinstance(v, date):
            return v
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                pass
        raise ValueError(f"unrecognized date {v!r}")


def validate_applicants(rows: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Return (clean_rows, schema_errors, normalized_date_count)."""
    clean, errors, normalized = [], [], 0
    for row in rows:
        if "/" in (row.get("birth_date") or ""):
            normalized += 1
        try:
            model = Applicant(**row)
        except ValidationError as exc:
            bad = {e["loc"][0] for e in exc.errors() if e["loc"]}
            for e in exc.errors():
                errors.append({"record_id": row.get("slate_ref"), "field": e["loc"][0] if e["loc"] else "",
                               "detail": e["msg"]})
            model = Applicant(**{k: (None if k in bad else v) for k, v in row.items()})
        d = model.model_dump()
        d["birth_date"] = d["birth_date"].isoformat() if d["birth_date"] else None
        clean.append(d)
    return clean, errors, normalized
