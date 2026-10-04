"""匹配引擎单元测试：pytest 或 python tests/test_matching.py 直接运行。"""

from __future__ import annotations

import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.matching import LogRecord, match_all  # noqa: E402

S = 1_000_000


def R(rid, home, peer, t_sec, band="20M", mode="SSB"):
    return LogRecord(rid, home, peer, band, mode, t_sec * S)


def pairs_of(res):
    return sorted((p.a, p.b) for p in res.pairs)


def test_mutual_reference_required():
    res = match_all([R("1", "A", "B", 0), R("2", "B", "A", 0)], 10 * S)
    assert pairs_of(res) == [("1", "2")]
    # 同向不能配
    res = match_all([R("1", "A", "B", 0), R("2", "A", "B", 0)], 10 * S)
    assert res.pairs == []
    assert res.unpaired == ["1", "2"]


def test_tolerance_boundary():
    # 时差 == 允许值：可配对
    res = match_all([R("1", "A", "B", 0), R("2", "B", "A", 60)], 60 * S)
    assert pairs_of(res) == [("1", "2")]
    # 超出：不可
    res = match_all([R("1", "A", "B", 0), R("2", "B", "A", 61)], 60 * S)
    assert res.pairs == []


def test_band_mode_must_match():
    res = match_all(
        [
            LogRecord("1", "A", "B", "20M", "SSB", 0),
            LogRecord("2", "B", "A", "40M", "SSB", 0),
        ],
        10 * S,
    )
    assert res.pairs == []
    res = match_all(
        [
            LogRecord("1", "A", "B", "20M", "SSB", 0),
            LogRecord("2", "B", "A", "20M", "CW", 0),
        ],
        10 * S,
    )
    assert res.pairs == []


def test_each_record_used_once_and_min_total_diff():
    # L1(t0) 与 R1(t2)/R2(t1)；L2(t0) 同。两种完美匹配总时差 3 vs 10
    res = match_all(
        [
            R("L1", "A", "B", 0),
            R("L2", "A", "B", 0),
            R("R1", "B", "A", 2),
            R("R2", "B", "A", 1),
        ],
        10 * S,
    )
    assert pairs_of(res) == [("L1", "R1"), ("L2", "R2")]


def test_cardinality_beats_lexicographic():
    # (a,x) 是字典序最小配对，但选它只能成 1 对；放弃它可成 2 对
    res = match_all(
        [
            R("a", "A", "B", 0),
            R("b", "A", "B", 5),
            R("x", "B", "A", 1),
            R("y", "B", "A", 0),
        ],
        4 * S,
    )
    assert pairs_of(res) == [("a", "y"), ("b", "x")]


def test_lexicographic_tie_break():
    res = match_all(
        [
            R("1", "A", "B", 0),
            R("2", "A", "B", 0),
            R("4", "B", "A", 0),
            R("3", "B", "A", 0),
        ],
        10 * S,
    )
    assert pairs_of(res) == [("1", "3"), ("2", "4")]
    assert sorted(p.time_diff_micros for p in res.pairs) == [0, 0]


def test_groups_are_independent():
    res = match_all(
        [
            R("1", "A", "B", 0),
            R("2", "B", "A", 0),
            R("3", "A", "C", 0),
            R("4", "C", "A", 0),
        ],
        10 * S,
    )
    assert pairs_of(res) == [("1", "2"), ("3", "4")]


def _bruteforce(left, right, tol_s):
    edges = []
    for i, lr in enumerate(left):
        for j, rr in enumerate(right):
            dt = abs(lr.time_micros - rr.time_micros) // S
            if dt <= tol_s:
                edges.append((i, j, dt))
    for k in range(min(len(left), len(right)), 0, -1):
        cands = []
        for combo in itertools.combinations(edges, k):
            if len({e[0] for e in combo}) == k and len({e[1] for e in combo}) == k:
                ps = sorted(
                    tuple(sorted((left[e[0]].record_id, right[e[1]].record_id)))
                    for e in combo
                )
                cands.append((sum(e[2] for e in combo), ps))
        if cands:
            cands.sort()
            return cands[0][1]
    return []


def test_random_against_bruteforce():
    random.seed(7)
    for _ in range(800):
        nl, nr = random.randint(1, 4), random.randint(1, 4)
        left = [R(f"l{i}", "A", "B", random.randint(0, 10)) for i in range(nl)]
        right = [R(f"r{j}", "B", "A", random.randint(0, 10)) for j in range(nr)]
        tol = random.choice([0, 1, 2, 5, 10])
        got = pairs_of(match_all(left + right, tol * S))
        expected = _bruteforce(left, right, tol)
        assert got == expected


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok -", fn.__name__)
    print(f"\nALL {len(fns)} ENGINE TESTS PASSED")
