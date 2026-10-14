"""Data-quality rules, written as plain SQL against the loaded SQLite database.

Each rule returns (record_id, detail). Rules are grouped by the office that owns
the fix, which is how the exception report is split.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .reference import VALID_PROGRAMS, VALID_STATES, VALID_TERM_STATUS, VALID_TERMS


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    system: str       # Slate | Colleague | Cross-system
    owner: str        # office that fixes it
    severity: str     # High | Medium | Low
    sql: str


RULES: list[Rule] = [
    # ---------------- Slate (admissions) ----------------
    Rule("R01", "Missing required name or email", "Slate", "Admissions", "High", """
        SELECT slate_ref, 'Missing: ' || TRIM(
            CASE WHEN first_name IS NULL THEN 'first name ' ELSE '' END ||
            CASE WHEN last_name  IS NULL THEN 'last name '  ELSE '' END ||
            CASE WHEN email      IS NULL THEN 'email'       ELSE '' END)
        FROM applicants
        WHERE first_name IS NULL OR last_name IS NULL OR email IS NULL"""),
    Rule("R02", "Missing birth date", "Slate", "Admissions", "Medium", """
        SELECT slate_ref, 'Birth date is blank' FROM applicants WHERE birth_date IS NULL"""),
    Rule("R03", "Invalid email format", "Slate", "Admissions", "High", r"""
        SELECT slate_ref, 'Email: ' || email FROM applicants
        WHERE email IS NOT NULL AND NOT regexp('^[^@\s]+@[^@\s]+\.[a-z]{2,}$', email)"""),
    Rule("R04", "Entry term not a valid term code", "Slate", "Admissions", "High", """
        SELECT slate_ref, 'Term: ' || entry_term FROM applicants
        WHERE entry_term IS NOT NULL AND entry_term NOT IN (SELECT code FROM ref_terms)"""),
    Rule("R05", "Program code not in program catalog", "Slate", "Admissions", "High", """
        SELECT slate_ref, 'Program: ' || program_code FROM applicants
        WHERE program_code IS NOT NULL AND program_code NOT IN (SELECT code FROM ref_programs)"""),
    Rule("R06", "Invalid state code", "Slate", "Admissions", "Low", """
        SELECT slate_ref, 'State: ' || state FROM applicants
        WHERE state IS NOT NULL AND state NOT IN (SELECT code FROM ref_states)"""),
    Rule("R07", "Implausible birth date", "Slate", "Admissions", "Medium", """
        SELECT slate_ref, 'Birth date: ' || birth_date FROM applicants
        WHERE birth_date IS NOT NULL AND (birth_date < :min_birth OR birth_date > :max_birth)"""),
    Rule("R08", "Same Slate record loaded more than once", "Slate", "Admissions", "Medium", """
        SELECT slate_ref, 'Appears ' || COUNT(*) || ' times' FROM applicants
        GROUP BY slate_ref HAVING COUNT(*) > 1"""),
    # ---------------- Colleague (registrar) ----------------
    Rule("R09", "Student missing birth date", "Colleague", "Registrar", "Medium", """
        SELECT student_id, 'BIRTH.DATE is blank' FROM students WHERE birth_date IS NULL"""),
    Rule("R10", "Term code not in term table", "Colleague", "Registrar", "High", """
        SELECT DISTINCT student_id, 'TERMS value ' || position || ': ' || term FROM student_terms
        WHERE term IS NOT NULL AND term NOT IN (SELECT code FROM ref_terms)"""),
    Rule("R11", "TERMS / TERM.STATUS association out of step", "Colleague", "Registrar", "High", """
        SELECT student_id, term_count || ' terms but ' || status_count || ' status values'
        FROM students WHERE term_count <> status_count"""),
    Rule("R12", "Invalid term status code", "Colleague", "Registrar", "Medium", """
        SELECT DISTINCT student_id, 'TERM.STATUS value ' || position || ': ' || status FROM student_terms
        WHERE status IS NOT NULL AND status NOT IN (SELECT code FROM ref_term_status)"""),
    # ---------------- Cross-system (integration) ----------------
    Rule("R13", "Deposited in Slate but not in Colleague", "Cross-system", "Integration", "High", """
        SELECT DISTINCT a.slate_ref, a.first_name || ' ' || a.last_name || ', ' || a.entry_term
        FROM applicants a
        WHERE a.app_status = 'Deposited'
          AND NOT EXISTS (SELECT 1 FROM students s WHERE s.slate_ref = a.slate_ref)"""),
    Rule("R14", "Colleague student with no Slate application", "Cross-system", "Integration", "Medium", """
        SELECT s.student_id, 'SLATE.REF: ' || COALESCE(s.slate_ref, '(blank)') FROM students s
        WHERE s.slate_ref IS NULL
           OR NOT EXISTS (SELECT 1 FROM applicants a WHERE a.slate_ref = s.slate_ref)"""),
    Rule("R15", "Birth date differs between systems", "Cross-system", "Integration", "High", """
        SELECT DISTINCT s.student_id, 'Slate ' || a.birth_date || ' vs Colleague ' || s.birth_date
        FROM students s JOIN applicants a ON a.slate_ref = s.slate_ref
        WHERE a.birth_date IS NOT NULL AND s.birth_date IS NOT NULL AND a.birth_date <> s.birth_date"""),
    Rule("R16", "Name differs between systems", "Cross-system", "Integration", "Medium", """
        SELECT DISTINCT s.student_id,
               'Slate ' || a.first_name || ' ' || a.last_name || ' vs Colleague ' || s.first_name || ' ' || s.last_name
        FROM students s JOIN applicants a ON a.slate_ref = s.slate_ref
        WHERE UPPER(TRIM(a.first_name)) <> UPPER(TRIM(s.first_name))
           OR UPPER(TRIM(a.last_name))  <> UPPER(TRIM(s.last_name))"""),
    Rule("R17", "Registered without a deposit in Slate", "Cross-system", "Integration", "High", """
        SELECT DISTINCT s.student_id, 'Slate status: ' || a.app_status
        FROM students s
        JOIN applicants a ON a.slate_ref = s.slate_ref
        JOIN student_terms t ON t.student_id = s.student_id
        WHERE t.status = 'R' AND a.app_status <> 'Deposited'"""),
]


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.create_function("regexp", 2, lambda pattern, value: value is not None
                         and re.search(pattern, value) is not None, deterministic=True)
    return conn


def load(conn: sqlite3.Connection, applicants: list[dict], students: list[dict], terms: list[dict]) -> None:
    pd.DataFrame(applicants).to_sql("applicants", conn, index=False, if_exists="replace")
    pd.DataFrame(students).to_sql("students", conn, index=False, if_exists="replace")
    pd.DataFrame(terms, columns=["student_id", "position", "term", "status"]).to_sql(
        "student_terms", conn, index=False, if_exists="replace")
    for table, values in [("ref_terms", VALID_TERMS), ("ref_programs", VALID_PROGRAMS),
                          ("ref_states", VALID_STATES), ("ref_term_status", VALID_TERM_STATUS)]:
        pd.DataFrame({"code": values}).to_sql(table, conn, index=False, if_exists="replace")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS ix_app_ref ON applicants(slate_ref);
        CREATE INDEX IF NOT EXISTS ix_stu_ref ON students(slate_ref);
        CREATE INDEX IF NOT EXISTS ix_terms_id ON student_terms(student_id);
    """)


def run_rules(conn: sqlite3.Connection, as_of: date, rules: list[Rule] = RULES) -> pd.DataFrame:
    params = {"min_birth": date(as_of.year - 90, as_of.month, 1).isoformat(),
              "max_birth": date(as_of.year - 14, as_of.month, 1).isoformat()}
    frames = []
    for rule in rules:
        rows = conn.execute(rule.sql, {k: v for k, v in params.items() if f":{k}" in rule.sql}).fetchall()
        df = pd.DataFrame(rows, columns=["record_id", "detail"])
        df.insert(0, "rule_id", rule.id)
        df.insert(1, "rule", rule.name)
        df.insert(2, "system", rule.system)
        df.insert(3, "owner", rule.owner)
        df.insert(4, "severity", rule.severity)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)
