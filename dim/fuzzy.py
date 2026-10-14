"""Fuzzy duplicate detection for person records.

Written from scratch (no third-party matcher) so every step can be explained:
  1. Blocking: only compare records that share a birth date, a phone number, or a
     Soundex code for last name plus first initial. Keeps 5,000 records to tens of
     thousands of comparisons instead of 12.5 million.
  2. Gate: first name (nicknames mapped to the formal name) and last name must
     both be at least 0.85 Jaro-Winkler similar, or the pair is dropped.
  3. Scoring: name similarity, then corroborating evidence from birth date, phone
     and email. A missing value counts as neutral, a conflicting one counts against.
  4. Banding: definite >= 0.93, likely >= 0.82. Likely pairs go to a review queue.
"""
from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations

NICKNAMES = {
    "jon": "john", "johnny": "john", "bill": "william", "will": "william", "liz": "elizabeth",
    "beth": "elizabeth", "bob": "robert", "rob": "robert", "mike": "michael", "kate": "katherine",
    "katie": "katherine", "jim": "james", "jimmy": "james", "alex": "alexander", "sam": "samuel",
    "dan": "daniel", "chris": "christopher", "matt": "matthew", "tony": "anthony",
    "nick": "nicholas", "joe": "joseph", "tom": "thomas", "jen": "jennifer", "jess": "jessica",
    "abby": "abigail", "maddie": "madison", "ben": "benjamin", "dave": "david",
    "steve": "steven", "andy": "andrew",
}

WEIGHTS = {"name": 0.45, "birth": 0.25, "phone": 0.20, "email": 0.10}
NAME_GATE = 0.85  # both first and last name must be at least this similar
DEFINITE, LIKELY = 0.93, 0.82


def jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0 if s1 else 0.0
    l1, l2 = len(s1), len(s2)
    if not l1 or not l2:
        return 0.0
    window = max(l1, l2) // 2 - 1
    m1, m2 = [False] * l1, [False] * l2
    matches = 0
    for i, c in enumerate(s1):
        for j in range(max(0, i - window), min(i + window + 1, l2)):
            if not m2[j] and s2[j] == c:
                m1[i] = m2[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = transpositions = 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1
    t = transpositions / 2
    return (matches / l1 + matches / l2 + (matches - t) / matches) / 3


def jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    s1, s2 = (s1 or "").lower(), (s2 or "").lower()
    j = jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1[:4], s2[:4]):
        if a != b:
            break
        prefix += 1
    return j + prefix * p * (1 - j)


def soundex(name: str) -> str:
    name = re.sub(r"[^a-z]", "", (name or "").lower())
    if not name:
        return ""
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
             **dict.fromkeys("dt", "3"), "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    out, prev = name[0].upper(), codes.get(name[0], "")
    for ch in name[1:]:
        code = codes.get(ch, "")
        if code and code != prev:
            out += code
        if ch not in "hw":
            prev = code
    return (out + "000")[:4]


def normalize_first(name: str | None) -> str:
    n = (name or "").strip().lower()
    return NICKNAMES.get(n, n)


def _email_local(email: str | None) -> str:
    local = (email or "").split("@")[0]
    return re.sub(r"\d+$", "", local)


def _agree(x, y) -> float:
    """1 = same, 0.5 = one side missing (no evidence either way), 0 = conflict."""
    if x and y:
        return 1.0 if x == y else 0.0
    return 0.5


def score_pair(a: dict, b: dict) -> tuple[float, str] | None:
    """Return (score, evidence) or None when the names are too different to be one person."""
    first = jaro_winkler(normalize_first(a["first_name"]), normalize_first(b["first_name"]))
    last = jaro_winkler(a["last_name"] or "", b["last_name"] or "")
    if first < NAME_GATE or last < NAME_GATE:
        return None
    birth = _agree(a["birth_date"], b["birth_date"])
    phone = _agree(a.get("phone"), b.get("phone"))
    email = jaro_winkler(_email_local(a["email"]), _email_local(b["email"])) if a["email"] and b["email"] else 0.5
    score = (WEIGHTS["name"] * (first + last) / 2 + WEIGHTS["birth"] * birth
             + WEIGHTS["phone"] * phone + WEIGHTS["email"] * email)
    reasons = []
    if (a["first_name"] or "").lower() != (b["first_name"] or "").lower():
        reasons.append("nickname" if first == 1.0 else f"first name {first:.2f}")
    if last < 1.0:
        reasons.append(f"last name {last:.2f}")
    reasons.append({1.0: "same birth date", 0.5: "birth date missing", 0.0: "birth date differs"}[birth])
    reasons.append({1.0: "same phone", 0.5: "phone missing", 0.0: "phone differs"}[phone])
    return round(score, 4), "; ".join(reasons)


def find_duplicates(records: list[dict]) -> list[dict]:
    """records need slate_ref, first_name, last_name, birth_date, email."""
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if r["birth_date"]:
            blocks["dob:" + r["birth_date"]].append(i)
        key = soundex(r["last_name"]) + normalize_first(r["first_name"])[:1]
        if len(key) == 5:
            blocks["sx:" + key].append(i)
        if r.get("phone"):
            blocks["ph:" + r["phone"]].append(i)

    seen, results = set(), []
    for members in blocks.values():
        for i, j in combinations(members, 2):
            a, b = records[i], records[j]
            if a["slate_ref"] == b["slate_ref"]:
                continue  # exact re-import, handled by rule R08
            pair = tuple(sorted((a["slate_ref"], b["slate_ref"])))
            if pair in seen:
                continue
            seen.add(pair)
            scored = score_pair(a, b)
            if scored is None:
                continue
            score, why = scored
            if score >= LIKELY:
                if a["slate_ref"] > b["slate_ref"]:
                    a, b = b, a
                results.append({
                    "record_a": a["slate_ref"], "record_b": b["slate_ref"], "score": score,
                    "band": "definite" if score >= DEFINITE else "likely",
                    "name_a": f"{a['first_name'] or ''} {a['last_name'] or ''}".strip(),
                    "name_b": f"{b['first_name'] or ''} {b['last_name'] or ''}".strip(),
                    "evidence": why,
                })
    results.sort(key=lambda r: -r["score"])
    return results
