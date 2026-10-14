"""Synthetic data generator.

Writes three files to a data directory:
  slate_applicants.csv   - an admissions CRM export (Slate-style)
  colleague_students.mv  - a student-records file in MultiValue format (Colleague/UniData-style)
  ground_truth.json      - every problem that was deliberately injected, so the
                           pipeline's detection can be measured (recall / precision)

No real people or institutions: all names, emails and IDs are made up.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from .multivalue import ENCODING, encode_record, to_internal_date
from .reference import VALID_PROGRAMS, VALID_STATES, VALID_TERMS

FIRST_NAMES = [
    "John", "William", "Elizabeth", "Robert", "Michael", "Katherine", "James", "Alexander",
    "Samuel", "Daniel", "Christopher", "Matthew", "Anthony", "Nicholas", "Joseph", "Thomas",
    "Jennifer", "Jessica", "Abigail", "Madison", "Mohammed", "Benjamin", "David", "Steven",
    "Andrew", "Olivia", "Sophia", "Isabella", "Mia", "Emily", "Grace", "Chloe", "Aaliyah",
    "Priya", "Wei", "Diego", "Mateo", "Lucia", "Fatima", "Omar", "Hannah", "Zoe", "Layla",
    "Ethan", "Noah", "Liam", "Aiden", "Carlos", "Ana", "Nora", "Kevin", "Brian", "Rachel",
    "Laura", "Hassan", "Yusuf", "Aisha", "Ravi", "Arjun", "Mei",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
    "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor",
    "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez",
    "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright",
    "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall",
    "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Khan", "Patel", "Shah", "Ahmed",
    "Kowalski", "Novak", "Murphy", "Sullivan", "Kim", "Park", "Chen", "Wang", "Singh", "Ali",
    "Cohen", "Fischer", "Schmidt", "Rossi", "Russo", "Okafor", "Mensah", "Diaz", "Reyes",
    "Morales", "Ortiz", "Gutierrez", "Ruiz", "Alvarez", "Castillo", "Jimenez",
]
NICKNAMES_OF = {
    "John": ["Jon", "Johnny"], "William": ["Bill", "Will"], "Elizabeth": ["Liz", "Beth"],
    "Robert": ["Bob", "Rob"], "Michael": ["Mike"], "Katherine": ["Kate", "Katie"],
    "James": ["Jim", "Jimmy"], "Alexander": ["Alex"], "Samuel": ["Sam"], "Daniel": ["Dan"],
    "Christopher": ["Chris"], "Matthew": ["Matt"], "Anthony": ["Tony"], "Nicholas": ["Nick"],
    "Joseph": ["Joe"], "Thomas": ["Tom"], "Jennifer": ["Jen"], "Jessica": ["Jess"],
    "Abigail": ["Abby"], "Madison": ["Maddie"], "Benjamin": ["Ben"], "David": ["Dave"],
    "Steven": ["Steve"], "Andrew": ["Andy"],
}
AREA_CODES = ["312", "773", "630", "708", "847", "331", "224"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "icloud.com", "hotmail.com"]

# Problems injected per 5,000 applicants at error_rate = 1.0
BASE_COUNTS = {
    "R01": 15, "R02": 40, "R03": 35, "R04": 20, "R05": 25, "R06": 20, "R07": 10, "R08": 12,
    "DUP": 60,
    "R09": 10, "R10": 12, "R11": 15, "R12": 8,
    "R13": 25, "R14": 10, "R15": 20, "R16": 18, "R17": 8,
}


def _typo(rng: random.Random, s: str) -> str:
    """Swap two adjacent inner letters (Martinez -> Mratinez style keying slip)."""
    if len(s) < 4:
        return s
    i = rng.randint(1, len(s) - 3)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


def generate(out_dir: str | Path, n: int = 5000, seed: int = 42, error_rate: float = 1.0,
             as_of: date = date(2026, 10, 1)) -> dict:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scale = n / 5000 * error_rate
    counts = {k: max(0, round(v * scale)) for k, v in BASE_COUNTS.items()}
    truth: list[dict] = []

    # ---- clean applicants -------------------------------------------------
    apps = []
    for i in range(n):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        age_days = rng.randint(17 * 365, 24 * 365) if rng.random() < 0.85 else rng.randint(25 * 365, 55 * 365)
        birth = as_of - timedelta(days=age_days)
        apps.append({
            "slate_ref": f"SL{100000 + i}",
            "first_name": first,
            "last_name": last,
            "birth_date": birth.isoformat(),
            "email": f"{first}.{last}{rng.randint(1, 999)}@{rng.choice(EMAIL_DOMAINS)}".lower(),
            "phone": f"{rng.choice(AREA_CODES)}-{rng.randint(200, 999)}-{rng.randint(0, 9999):04d}",
            "entry_term": rng.choice(VALID_TERMS[:4]),
            "program_code": rng.choice(VALID_PROGRAMS),
            "state": "IL" if rng.random() < 0.7 else rng.choice(VALID_STATES),
            "app_status": rng.choices(["Submitted", "Admitted", "Denied", "Deposited"],
                                      weights=[35, 25, 15, 25])[0],
            "colleague_id": "",
        })

    deposited = [a for a in apps if a["app_status"] == "Deposited"]
    others = [a for a in apps if a["app_status"] != "Deposited"]
    rng.shuffle(deposited)
    rng.shuffle(others)

    def take(pool: list, k: int) -> list:
        picked = pool[:k]
        del pool[:k]
        return picked

    # 5% of birth dates arrive as MM/DD/YYYY - valid, but must be normalized on load.
    for a in rng.sample(apps, k=n // 20):
        y, m, d = a["birth_date"].split("-")
        a["birth_date"] = f"{m}/{d}/{y}"

    # ---- Slate-side problems (drawn from non-deposited applicants) --------
    for a in take(others, counts["R01"]):
        a[rng.choice(["first_name", "last_name", "email"])] = ""
        truth.append({"issue": "R01", "record_id": a["slate_ref"]})
    for a in take(others, counts["R02"]):
        a["birth_date"] = ""
        truth.append({"issue": "R02", "record_id": a["slate_ref"]})
    for a in take(others, counts["R03"]):
        local = a["email"].split("@")[0]
        a["email"] = rng.choice([f"{local}@gmail", f"{local} gmail.com", f"{local}@@yahoo.com",
                                 f"{local}.gmail.com"])
        truth.append({"issue": "R03", "record_id": a["slate_ref"]})
    for a in take(others, counts["R04"]):
        a["entry_term"] = rng.choice(["FA2027", "2027F", "2029WI", "2027FALL"])
        truth.append({"issue": "R04", "record_id": a["slate_ref"]})
    for a in take(others, counts["R05"]):
        a["program_code"] = rng.choice(["BS.CSCI", "BS.NURSE", "UNDECL", "BA.PSYCH"])
        truth.append({"issue": "R05", "record_id": a["slate_ref"]})
    for a in take(others, counts["R06"]):
        a["state"] = rng.choice(["XX", "ILL", "Illinois", "I1"])
        truth.append({"issue": "R06", "record_id": a["slate_ref"]})
    for a in take(others, counts["R07"]):
        a["birth_date"] = rng.choice(["1900-01-01", "2024-05-17", "1925-07-04", "2019-02-11"])
        truth.append({"issue": "R07", "record_id": a["slate_ref"]})

    extra_rows = []
    for a in take(others, counts["R08"]):           # same row imported twice
        extra_rows.append(dict(a))
        truth.append({"issue": "R08", "record_id": a["slate_ref"]})

    next_ref = 100000 + n
    for a in take(others, counts["DUP"]):           # same person, second application
        variant = rng.random()
        first = a["first_name"]
        if variant < 0.5 and first in NICKNAMES_OF:
            first = rng.choice(NICKNAMES_OF[first])
        elif variant < 0.8:
            first = _typo(rng, first)
        last = _typo(rng, a["last_name"]) if rng.random() < 0.3 else a["last_name"]
        dup = dict(a)
        area, mid, end = a["phone"].split("-")
        dup.update({
            "phone": f"({area}) {mid}-{end}",   # same number, different formatting
            "slate_ref": f"SL{next_ref}",
            "first_name": first,
            "last_name": last,
            "email": f"{first}.{last}@{rng.choice(EMAIL_DOMAINS)}".lower(),
            "birth_date": a["birth_date"] if rng.random() < 0.85 else "",
            "app_status": "Submitted",
        })
        next_ref += 1
        extra_rows.append(dup)
        if not dup["birth_date"]:
            truth.append({"issue": "R02", "record_id": dup["slate_ref"]})
        truth.append({"issue": "DUP", "record_id": f"{a['slate_ref']}|{dup['slate_ref']}"})

    # ---- Colleague (student records) --------------------------------------
    records: list[tuple[str, list]] = []
    next_cid = 1000000

    def new_cid() -> str:
        nonlocal next_cid
        next_cid += 1
        return f"{next_cid:07d}"

    def iso(a: dict) -> date:
        raw = a["birth_date"]
        if "/" in raw:
            m, d, y = raw.split("/")
            return date(int(y), int(m), int(d))
        return date.fromisoformat(raw)

    def student_attrs(a: dict, terms=None, statuses=None) -> list:
        t_idx = VALID_TERMS.index(a["entry_term"])
        terms = terms or VALID_TERMS[t_idx:t_idx + 2]
        statuses = statuses or ["R"] * len(terms)
        school = f"{a['first_name'][0]}{a['last_name']}@students.demo.edu".lower()
        return [a["last_name"], a["first_name"], to_internal_date(iso(a)), [a["email"], school],
                a["slate_ref"], terms, statuses, a["program_code"]]

    # Deposited students who never made it across (sync failure).
    for a in take(deposited, counts["R13"]):
        truth.append({"issue": "R13", "record_id": a["slate_ref"]})

    special = {k: take(deposited, counts[k]) for k in ["R09", "R10", "R11", "R12", "R15", "R16"]}
    for a in deposited:                              # the clean majority
        a["colleague_id"] = new_cid()
        records.append((a["colleague_id"], student_attrs(a)))

    for issue, group in special.items():
        for a in group:
            cid = new_cid()
            a["colleague_id"] = cid
            attrs = student_attrs(a)
            if issue == "R09":
                attrs[2] = ""
            elif issue == "R10":
                attrs[5] = [attrs[5][0], rng.choice(["2027XX", "27FA", "2031FA"])]
            elif issue == "R11":
                attrs[6] = attrs[6][:1]  # two terms, one status: association broken
            elif issue == "R12":
                attrs[6] = ["R", rng.choice(["X", "Q", "?"])]
            elif issue == "R15":
                attrs[2] = to_internal_date(iso(a) + timedelta(days=rng.choice([-365, 1, 30, 31])))
            elif issue == "R16":
                attrs[0] = f"{a['last_name']}-{rng.choice(LAST_NAMES)}"
            records.append((cid, attrs))
            truth.append({"issue": issue, "record_id": cid})

    # Colleague students with no Slate application behind them.
    for _ in range(counts["R14"]):
        cid = new_cid()
        fake = {"first_name": rng.choice(FIRST_NAMES), "last_name": rng.choice(LAST_NAMES),
                "birth_date": (as_of - timedelta(days=rng.randint(6500, 8500))).isoformat(),
                "email": "", "slate_ref": rng.choice(["", f"SL{rng.randint(900000, 999999)}"]),
                "entry_term": VALID_TERMS[1], "program_code": rng.choice(VALID_PROGRAMS)}
        records.append((cid, student_attrs(fake)))
        truth.append({"issue": "R14", "record_id": cid})

    # Registered in Colleague although Slate never shows a deposit.
    for a in take(others, counts["R17"]):
        cid = new_cid()
        records.append((cid, student_attrs(a)))
        truth.append({"issue": "R17", "record_id": cid})

    rng.shuffle(records)

    # ---- write ------------------------------------------------------------
    rows = apps + extra_rows
    rng.shuffle(rows)
    with open(out / "slate_applicants.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with open(out / "colleague_students.mv", "w", encoding=ENCODING, newline="\n") as fh:
        for cid, attrs in records:
            fh.write(encode_record(cid, attrs) + "\n")
    meta = {"seed": seed, "n": n, "error_rate": error_rate, "as_of": as_of.isoformat(),
            "slate_rows": len(rows), "colleague_records": len(records), "injected": counts}
    (out / "ground_truth.json").write_text(json.dumps({"meta": meta, "issues": truth}, indent=1))
    return meta


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Generate synthetic Slate + Colleague data")
    p.add_argument("--out", default="data")
    p.add_argument("--n", type=int, default=5000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--error-rate", type=float, default=1.0)
    args = p.parse_args()
    print(json.dumps(generate(args.out, args.n, args.seed, args.error_rate), indent=1))
