import tempfile
import unittest
from datetime import date
from pathlib import Path

from dim.multivalue import (AM, ENCODING, VM, decode_record, encode_record, from_internal_date,
                            parse_students_file, to_internal_date)


class InternalDates(unittest.TestCase):
    def test_pick_epoch(self):
        # Day 0 in Pick/UniData is 31 Dec 1967; day 1 is 1 Jan 1968.
        self.assertEqual(from_internal_date("0"), date(1967, 12, 31))
        self.assertEqual(from_internal_date("1"), date(1968, 1, 1))

    def test_round_trip(self):
        for d in [date(2000, 2, 29), date(2008, 11, 4), date(1950, 6, 1)]:
            self.assertEqual(from_internal_date(to_internal_date(d)), d)

    def test_blank_and_bad(self):
        self.assertIsNone(from_internal_date(""))
        with self.assertRaises(ValueError):
            from_internal_date("2008-11-04")


class Records(unittest.TestCase):
    def test_encode_decode(self):
        line = encode_record("0001234", ["Smith", "Ana", ["a@x.com", "b@y.edu"]])
        self.assertEqual(line, f"0001234{AM}Smith{AM}Ana{AM}a@x.com{VM}b@y.edu")
        rid, attrs = decode_record(line)
        self.assertEqual(rid, "0001234")
        self.assertEqual(attrs[2], ["a@x.com", "b@y.edu"])

    def _parse(self, *records):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "students.mv"
            path.write_text("\n".join(encode_record(r, a) for r, a in records) + "\n", encoding=ENCODING)
            return parse_students_file(path)

    def test_association_flattened(self):
        attrs = ["Lee", "Mei", to_internal_date(date(2008, 1, 2)), ["mei@x.com", "mlee@s.edu"],
                 "SL1", ["2026FA", "2027SP"], ["R", "W"], "BS.CS"]
        p = self._parse(("0000001", attrs))
        s = p.students[0]
        self.assertEqual(s["birth_date"], "2008-01-02")
        self.assertEqual(s["school_email"], "mlee@s.edu")
        self.assertEqual((s["term_count"], s["status_count"]), (2, 2))
        self.assertEqual([(t["term"], t["status"]) for t in p.terms], [("2026FA", "R"), ("2027SP", "W")])

    def test_broken_association_kept_visible(self):
        attrs = ["Lee", "Mei", "", ["m@x.com"], "SL1", ["2026FA", "2027SP"], ["R"], "BS.CS"]
        p = self._parse(("0000002", attrs))
        self.assertEqual((p.students[0]["term_count"], p.students[0]["status_count"]), (2, 1))
        self.assertIsNone(p.terms[1]["status"])
        self.assertIsNone(p.students[0]["birth_date"])

    def test_bad_internal_date_is_logged_not_fatal(self):
        attrs = ["Lee", "Mei", "11/04/2008", ["m@x.com"], "SL1", ["2026FA"], ["R"], "BS.CS"]
        p = self._parse(("0000003", attrs))
        self.assertEqual(len(p.parse_errors), 1)
        self.assertIsNone(p.students[0]["birth_date"])


if __name__ == "__main__":
    unittest.main()
