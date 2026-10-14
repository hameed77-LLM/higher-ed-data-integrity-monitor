"""Each SQL rule is checked against a tiny hand-built dataset: one record that should
trip it and clean records that should not."""
import unittest
from datetime import date

from dim.models import validate_applicants
from dim.rules import RULES, connect, load, run_rules

AS_OF = date(2026, 10, 1)


def app(ref, **kw):
    row = {"slate_ref": ref, "first_name": "Ana", "last_name": "Lee", "birth_date": "2008-01-02",
           "email": f"{ref.lower()}@gmail.com", "phone": "312-555-0100", "entry_term": "2027FA",
           "program_code": "BS.CS", "state": "IL", "app_status": "Submitted", "colleague_id": ""}
    row.update(kw)
    return row


def stu(sid, slate_ref, **kw):
    row = {"student_id": sid, "last_name": "Lee", "first_name": "Ana", "birth_date": "2008-01-02",
           "personal_email": "x@gmail.com", "school_email": "alee@s.edu", "slate_ref": slate_ref,
           "program": "BS.CS", "term_count": 1, "status_count": 1}
    row.update(kw)
    return row


class RuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = [
            app("CLEAN1", app_status="Deposited"),
            app("CLEAN2"),
            app("R01", last_name=""),
            app("R02", birth_date=""),
            app("R03", email="ana.lee@gmail"),
            app("R04", entry_term="FA2027"),
            app("R05", program_code="BS.CSCI"),
            app("R06", state="Illinois"),
            app("R07", birth_date="1900-01-01"),
            app("R08"), app("R08"),
            app("R13", app_status="Deposited"),
            app("DEP15", app_status="Deposited"),
            app("DEP16", app_status="Deposited"),
            app("ADM17", app_status="Admitted"),
            app("DEP09", app_status="Deposited"),
            app("DEP10", app_status="Deposited"),
            app("DEP11", app_status="Deposited"),
            app("DEP12", app_status="Deposited"),
        ]
        applicants, cls.schema_errors, _ = validate_applicants(raw)
        students = [
            stu("C-CLEAN", "CLEAN1"),
            stu("C-R09", "DEP09", birth_date=None),
            stu("C-R10", "DEP10"),
            stu("C-R11", "DEP11", term_count=2, status_count=1),
            stu("C-R12", "DEP12"),
            stu("C-R14", "SL999999"),
            stu("C-R15", "DEP15", birth_date="2008-02-01"),
            stu("C-R16", "DEP16", last_name="Lee-Park"),
            stu("C-R17", "ADM17"),
        ]
        terms = [{"student_id": s["student_id"], "position": 1, "term": "2027FA", "status": "R"}
                 for s in students if s["student_id"] not in ("C-R10", "C-R12")]
        terms += [{"student_id": "C-R10", "position": 1, "term": "2027XX", "status": "R"},
                  {"student_id": "C-R12", "position": 1, "term": "2027FA", "status": "Q"},
                  {"student_id": "C-R11", "position": 2, "term": "2028SP", "status": None}]
        conn = connect()
        load(conn, applicants, students, terms)
        cls.found = run_rules(conn, AS_OF)

    def ids(self, rule_id):
        return set(self.found[self.found.rule_id == rule_id].record_id)

    def test_every_rule_fires_exactly_on_its_record(self):
        expected = {
            "R01": {"R01"}, "R02": {"R02"}, "R03": {"R03"}, "R04": {"R04"}, "R05": {"R05"},
            "R06": {"R06"}, "R07": {"R07"}, "R08": {"R08"}, "R09": {"C-R09"}, "R10": {"C-R10"},
            "R11": {"C-R11"}, "R12": {"C-R12"}, "R13": {"R13"}, "R14": {"C-R14"},
            "R15": {"C-R15"}, "R16": {"C-R16"}, "R17": {"C-R17"},
        }
        self.assertEqual(set(expected), {r.id for r in RULES})
        for rule_id, ids in expected.items():
            with self.subTest(rule=rule_id):
                self.assertEqual(self.ids(rule_id), ids)

    def test_clean_records_never_flagged(self):
        self.assertFalse({"CLEAN1", "CLEAN2", "C-CLEAN"} & set(self.found.record_id))

    def test_normalization_prevents_false_alarms(self):
        rows, errors, normalized = validate_applicants([
            app("N1", birth_date="01/02/2008", email="  Ana.Lee@GMAIL.com ", entry_term="2027fa",
                phone="(312) 555-0100")])
        self.assertEqual(errors, [])
        self.assertEqual(normalized, 1)
        self.assertEqual(rows[0]["birth_date"], "2008-01-02")
        self.assertEqual(rows[0]["email"], "ana.lee@gmail.com")
        self.assertEqual(rows[0]["entry_term"], "2027FA")
        self.assertEqual(rows[0]["phone"], "3125550100")

    def test_bad_type_becomes_schema_error_not_crash(self):
        rows, errors, _ = validate_applicants([app("S1", birth_date="next tuesday", app_status="Maybe")])
        self.assertEqual({e["field"] for e in errors}, {"birth_date", "app_status"})
        self.assertIsNone(rows[0]["birth_date"])


if __name__ == "__main__":
    unittest.main()
