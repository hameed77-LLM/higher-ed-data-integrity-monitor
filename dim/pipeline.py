"""Nightly data-integrity run: load -> validate -> rules -> dedupe -> reconcile -> report.

    python -m dim.pipeline                     # generate fresh demo data, run once
    python -m dim.pipeline --no-generate       # run against files already in data/
    python -m dim.pipeline --backfill 30       # simulate 30 nightly runs to build a trend
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .fuzzy import find_duplicates
from .generate import generate
from .models import validate_applicants
from .multivalue import parse_students_file
from .report import write_dashboard, write_excel
from .rules import RULES, connect, load, run_rules

HISTORY_COLUMNS = ["run_date", "records_checked", "open_exceptions", "high_severity",
                   "possible_duplicates", "quality_score", "sync_rate"]


def evaluate(exceptions: pd.DataFrame, duplicates: list[dict], truth_path: Path) -> list[dict]:
    """Compare what the pipeline found with what the generator injected."""
    if not truth_path.exists():
        return []
    truth = json.loads(truth_path.read_text())["issues"]
    expected: dict[str, set] = {}
    for t in truth:
        key = frozenset(t["record_id"].split("|")) if t["issue"] == "DUP" else t["record_id"]
        expected.setdefault(t["issue"], set()).add(key)
    found = {rid: set(g.record_id) for rid, g in exceptions.groupby("rule_id")}
    found["DUP"] = {frozenset((d["record_a"], d["record_b"])) for d in duplicates}
    rows = []
    for rid in [r.id for r in RULES] + ["DUP"]:
        e, f = expected.get(rid, set()), found.get(rid, set())
        tp = len(e & f)
        rows.append({"check": rid, "injected": len(e), "flagged": len(f), "caught": tp,
                     "recall": tp / len(e) if e else None, "precision": tp / len(f) if f else None})
    return rows


def run(data_dir: Path, out_dir: Path, as_of: date, append_history: bool = True,
        write_reports: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(data_dir / "slate_applicants.csv", newline="", encoding="utf-8") as fh:
        raw_apps = list(csv.DictReader(fh))
    applicants, schema_errors, normalized_dates = validate_applicants(raw_apps)
    parsed = parse_students_file(data_dir / "colleague_students.mv")

    db_path = out_dir / "integrity.sqlite"
    db_path.unlink(missing_ok=True)
    conn = connect(str(db_path))
    load(conn, applicants, parsed.students, parsed.terms)
    exceptions = run_rules(conn, as_of)

    extra = [{"rule_id": "R00", "rule": "Schema / type error on load", "system": "Slate",
              "owner": "Admissions", "severity": "High", "record_id": e["record_id"],
              "detail": f"{e['field']}: {e['detail']}"} for e in schema_errors]
    extra += [{"rule_id": "R00", "rule": "Schema / type error on load", "system": "Colleague",
               "owner": "Registrar", "severity": "High", "record_id": e["record_id"],
               "detail": e["error"]} for e in parsed.parse_errors]
    if extra:
        exceptions = pd.concat([pd.DataFrame(extra), exceptions], ignore_index=True)

    unique_apps = list({a["slate_ref"]: a for a in applicants}.values())
    duplicates = find_duplicates(unique_apps)

    # Reconciliation figures
    deposited = conn.execute("SELECT COUNT(DISTINCT slate_ref) FROM applicants WHERE app_status='Deposited'").fetchone()[0]
    matched = conn.execute("""SELECT COUNT(DISTINCT a.slate_ref) FROM applicants a
                              JOIN students s ON s.slate_ref = a.slate_ref
                              WHERE a.app_status='Deposited'""").fetchone()[0]
    linked = conn.execute("SELECT COUNT(DISTINCT s.student_id) FROM students s JOIN applicants a ON a.slate_ref = s.slate_ref").fetchone()[0]
    conn.close()

    total_records = len(unique_apps) + len(parsed.students)
    flagged = set(exceptions.record_id) | {d["record_b"] for d in duplicates}
    evaluation = evaluate(exceptions, duplicates, data_dir / "ground_truth.json")

    by_rule = (exceptions.groupby(["rule_id", "rule", "system", "owner", "severity"]).size()
               .rename("count").reset_index())
    catalog = pd.DataFrame([{"rule_id": r.id, "rule": r.name, "system": r.system, "owner": r.owner,
                             "severity": r.severity} for r in RULES])
    by_rule = catalog.merge(by_rule[["rule_id", "count"]], on="rule_id", how="left").fillna({"count": 0})
    by_rule["count"] = by_rule["count"].astype(int)

    metrics = {
        "run_date": as_of.isoformat(),
        "slate_rows": len(applicants),
        "slate_applicants": len(unique_apps),
        "colleague_records": len(parsed.students),
        "records_checked": total_records,
        "rules": len(RULES),
        "open_exceptions": int(len(exceptions)),
        "high_severity": int((exceptions.severity == "High").sum()),
        "records_flagged": len(flagged),
        "quality_score": round(1 - len(flagged) / total_records, 4),
        "possible_duplicates": len(duplicates),
        "definite_duplicates": sum(d["band"] == "definite" for d in duplicates),
        "dates_normalized": normalized_dates,
        "deposited": deposited,
        "deposited_in_colleague": matched,
        "sync_rate": round(matched / deposited, 4) if deposited else None,
        "colleague_linked_to_slate": linked,
        "by_owner": exceptions.groupby("owner").size().to_dict(),
    }

    history_path = out_dir / "run_history.csv"
    if append_history:
        row = {k: metrics[k] for k in HISTORY_COLUMNS}
        hist = pd.read_csv(history_path) if history_path.exists() else pd.DataFrame(columns=HISTORY_COLUMNS)
        hist = hist[hist.run_date != row["run_date"]]
        hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True).sort_values("run_date")
        hist.to_csv(history_path, index=False)
    history = pd.read_csv(history_path) if history_path.exists() else pd.DataFrame(columns=HISTORY_COLUMNS)

    if write_reports:
        exceptions.to_csv(out_dir / "exceptions.csv", index=False)
        pd.DataFrame(duplicates).to_csv(out_dir / "duplicate_review.csv", index=False)
        (out_dir / "metrics.json").write_text(json.dumps({**metrics, "evaluation": evaluation}, indent=1))
        write_excel(out_dir / f"exception_report_{as_of.isoformat()}.xlsx", metrics, by_rule,
                    exceptions, duplicates, evaluation)
        write_dashboard(out_dir / "dashboard.html", metrics, by_rule, history, duplicates, evaluation)
    return {**metrics, "evaluation": evaluation}


def main() -> None:
    p = argparse.ArgumentParser(description="Higher-ed data integrity monitor")
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="output")
    p.add_argument("--as-of", default=date.today().isoformat())
    p.add_argument("--n", type=int, default=5000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--error-rate", type=float, default=1.0)
    p.add_argument("--no-generate", action="store_true", help="use files already in --data")
    p.add_argument("--backfill", type=int, default=0,
                   help="simulate N earlier nightly runs (error rate falling as records get fixed)")
    args = p.parse_args()

    data_dir, out_dir, as_of = Path(args.data), Path(args.out), date.fromisoformat(args.as_of)
    if args.backfill:
        (out_dir / "run_history.csv").unlink(missing_ok=True)
        for i in range(args.backfill):
            day = as_of - timedelta(days=args.backfill - i)
            rate = 1.8 - 1.1 * i / max(1, args.backfill - 1) + (0.15 if i % 7 == 0 else 0)
            generate(data_dir, args.n, args.seed + i + 1, rate, day)
            run(data_dir, out_dir, day, write_reports=False)
    if not args.no_generate:
        generate(data_dir, args.n, args.seed, args.error_rate, as_of)
    m = run(data_dir, out_dir, as_of)

    print(f"Run {m['run_date']}: {m['records_checked']:,} records checked against {m['rules']} rules")
    print(f"  open exceptions   {m['open_exceptions']:,} ({m['high_severity']} high severity)")
    print(f"  possible dupes    {m['possible_duplicates']} ({m['definite_duplicates']} definite)")
    print(f"  Slate->Colleague  {m['deposited_in_colleague']}/{m['deposited']} deposited students synced "
          f"({m['sync_rate']:.1%})")
    print(f"  quality score     {m['quality_score']:.1%} of records clean")
    if m["evaluation"]:
        caught = sum(r["caught"] for r in m["evaluation"])
        injected = sum(r["injected"] for r in m["evaluation"])
        flagged = sum(r["flagged"] for r in m["evaluation"])
        print(f"  detection         caught {caught}/{injected} injected issues, {flagged - caught} false alarms")
    print(f"  reports           {out_dir}/dashboard.html, {out_dir}/exception_report_{m['run_date']}.xlsx")


if __name__ == "__main__":
    main()
