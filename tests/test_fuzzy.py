import unittest

from dim.fuzzy import find_duplicates, jaro_winkler, normalize_first, score_pair, soundex


def person(ref, first, last, birth="2008-03-09", phone="3125550101", email=None):
    return {"slate_ref": ref, "first_name": first, "last_name": last, "birth_date": birth,
            "phone": phone, "email": email or f"{first}.{last}@gmail.com".lower()}


class StringMeasures(unittest.TestCase):
    def test_jaro_winkler_reference_values(self):
        # Textbook values from Winkler (1990)
        self.assertAlmostEqual(jaro_winkler("MARTHA", "MARHTA"), 0.961, places=3)
        self.assertAlmostEqual(jaro_winkler("DWAYNE", "DUANE"), 0.840, places=3)
        self.assertAlmostEqual(jaro_winkler("DIXON", "DICKSONX"), 0.813, places=3)
        self.assertEqual(jaro_winkler("same", "same"), 1.0)
        self.assertEqual(jaro_winkler("", "x"), 0.0)

    def test_soundex(self):
        self.assertEqual(soundex("Robert"), "R163")
        self.assertEqual(soundex("Rupert"), "R163")
        self.assertEqual(soundex("Ashcraft"), "A261")
        self.assertEqual(soundex("Tymczak"), "T522")

    def test_nicknames(self):
        self.assertEqual(normalize_first("Bill"), "william")
        self.assertEqual(normalize_first(" Jon "), "john")
        self.assertEqual(normalize_first("Priya"), "priya")


class Matching(unittest.TestCase):
    def test_nickname_same_dob_same_phone_is_definite(self):
        score, why = score_pair(person("A", "William", "Garcia"), person("B", "Bill", "Garcia"))
        self.assertGreaterEqual(score, 0.93)
        self.assertIn("nickname", why)

    def test_typo_with_missing_dob_is_likely(self):
        score, _ = score_pair(person("A", "Joseph", "Martinez"), person("B", "Joseph", "Matrinez", birth=None))
        self.assertTrue(0.82 <= score < 0.93)

    def test_different_people_same_birthday_rejected(self):
        self.assertIsNone(score_pair(person("A", "Ana", "Russo"), person("B", "Ana", "Okafor")))

    def test_same_name_different_dob_and_phone_not_flagged(self):
        a = person("A", "John", "Smith")
        b = person("B", "John", "Smith", birth="2005-07-01", phone="7735550199")
        score, _ = score_pair(a, b)
        self.assertLess(score, 0.82)

    def test_find_duplicates_ignores_exact_reimport(self):
        rows = [person("SL1", "Mei", "Lee"), person("SL1", "Mei", "Lee"), person("SL2", "Mei", "Leee")]
        dups = find_duplicates(rows)
        self.assertEqual([(d["record_a"], d["record_b"]) for d in dups], [("SL1", "SL2")])


if __name__ == "__main__":
    unittest.main()
