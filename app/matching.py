"""通联日志互证匹配引擎。

规则（按优先级）：
1. 相互指认：A 日志的本台/对方恰为 B 日志的对方/本台；
2. 波段、模式相同；
3. |tA - tB| <= 演练允许时差；
4. 每条记录最多参与一对。

全场优化目标（按字典序）：
(1) 最大化配对数；
(2) 在配对数最大的解中最小化总时差；
(3) 仍并列时，将每对的两个记录号升序排列 (a,b)，再将所有配对整体
    升序，取字典序最小的配对列表。

结构性质：把日志按无序呼号对 {本台,对方}、波段、模式分组后，每组内
两侧天然构成二分图（左侧本台=较小呼号，右侧本台=较大呼号），组间不
共享记录，相互独立，可分别求解。

算法：每组跑一次最小费用最大流（SSP + Dijkstra 势）。先把流量推到
最大（配对数最大），同流量下费用最小即“总时差 + 字典序”的最优。

费用编码（纯整数，无浮点误差）：
    edge_cost = W * |dt微秒| - 2 ** (E-1-rank)
其中 E 为该组可行边数，W = 2**E，rank 为该边规范化配对 (min记录号,
max记录号) 的字典序排名。
- 时差项整数、最小正差为 W，大于所有位权之和 (2^E-1)，故总时差差
  异永远压过字典序差异；
- 位权满足 w_rank > 之后所有权重之和，“含最小差异配对”的方案费用
  更低（等价于对配对列表做字典序最小选择，见 README 的推导）。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class LogRecord:
    """单条日志（字段均已规范化；time_micros 为 UTC epoch 微秒）。"""

    record_id: str
    home: str
    peer: str
    band: str
    mode: str
    time_micros: int


@dataclass(frozen=True)
class Pair:
    """一对互证记录；a/b 已按记录号升序排列。"""

    a: str
    b: str
    time_diff_micros: int

    def canonical(self) -> Tuple[str, str]:
        return (self.a, self.b)


@dataclass
class MatchingResult:
    pairs: List[Pair]
    unpaired: List[str]


def canonical_pair_id(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _group_key(rec: LogRecord) -> Tuple[str, str, str, str]:
    lo, hi = (rec.home, rec.peer) if rec.home < rec.peer else (rec.peer, rec.home)
    return (lo, hi, rec.band, rec.mode)


def _is_left(rec: LogRecord) -> bool:
    """左侧：本台呼号为无序对中的较小者。"""
    return rec.home < rec.peer


def match_all(records: Sequence[LogRecord], tolerance_micros: int) -> MatchingResult:
    """计算一场演练的全场最优互证结果。"""
    groups: Dict[Tuple[str, str, str, str], List[LogRecord]] = {}
    for rec in records:
        groups.setdefault(_group_key(rec), []).append(rec)

    all_pairs: List[Pair] = []
    paired: set[str] = set()

    for recs in groups.values():
        left = [r for r in recs if _is_left(r)]
        right = [r for r in recs if not _is_left(r)]
        if not left or not right:
            continue
        for p in _match_one_group(left, right, tolerance_micros):
            paired.add(p.a)
            paired.add(p.b)
            all_pairs.append(p)

    all_pairs.sort(key=lambda p: p.canonical())
    unpaired = sorted(r.record_id for r in records if r.record_id not in paired)
    return MatchingResult(pairs=all_pairs, unpaired=unpaired)


@dataclass
class _FEdge:
    u: int
    v: int
    cap: int
    cost: int


def _match_one_group(
    left: List[LogRecord],
    right: List[LogRecord],
    tolerance_micros: int,
) -> List[Pair]:
    # ---- 1. 可行边 -------------------------------------------------------
    @dataclass(frozen=True)
    class E:
        li: int
        ri: int
        dt: int

    edges: List[E] = []
    left_has: Dict[int, bool] = {}
    right_has: set[int] = set()
    for li, lr in enumerate(left):
        for ri, rr in enumerate(right):
            dt = abs(lr.time_micros - rr.time_micros)
            if dt <= tolerance_micros:
                edges.append(E(li, ri, dt))
                left_has[li] = True
                right_has.add(ri)
    if not edges:
        return []

    active_left = sorted(left_has)
    active_right = sorted(right_has)

    # rank: 规范化配对记录号字典序排名（0 = 最小配对）
    def rec_pair(e: E) -> Tuple[str, str]:
        x, y = left[e.li].record_id, right[e.ri].record_id
        return (x, y) if x <= y else (y, x)

    order = sorted(range(len(edges)), key=lambda i: rec_pair(edges[i]))
    rank = [0] * len(edges)
    for pos, ei in enumerate(order):
        rank[ei] = pos

    ne = len(edges)
    W = 1 << ne  # 2^E

    # ---- 2. 建网络 S -> L -> R -> T，容量均 1 ----------------------------
    nl, nr = len(active_left), len(active_right)
    S = 0
    L0 = 1
    R0 = L0 + nl
    T = R0 + nr
    n = T + 1

    lnode = {li: L0 + i for i, li in enumerate(active_left)}
    rnode = {ri: R0 + i for i, ri in enumerate(active_right)}

    fedges: List[_FEdge] = []
    adj: List[List[int]] = [[] for _ in range(n)]

    def add(u: int, v: int, cap: int, cost: int) -> int:
        idx = len(fedges)
        fedges.append(_FEdge(u, v, cap, cost))
        adj[u].append(idx)
        fedges.append(_FEdge(v, u, 0, -cost))
        adj[v].append(idx ^ 1)
        return idx

    for li in active_left:
        add(S, lnode[li], 1, 0)
    for ri in active_right:
        add(rnode[ri], T, 1, 0)

    mid: List[int] = []
    for ei, e in enumerate(edges):
        bit = 1 << (ne - 1 - rank[ei])
        mid.append(add(lnode[e.li], rnode[e.ri], 1, W * e.dt - bit))

    # ---- 3. 初始势：初始残量网络是 DAG（S,L,R,T），直接求最短路 -----------
    INF = 1 << (2 * ne + 64)
    pi = [INF] * n
    pi[S] = 0
    for li in active_left:
        pi[lnode[li]] = 0
    for ri in active_right:
        rn = rnode[ri]
        best = INF
        for ei, e in enumerate(edges):
            if e.ri == ri:
                best = min(best, pi[lnode[e.li]] + fedges[mid[ei]].cost)
        pi[rn] = best
    pi[T] = min(pi[rnode[ri]] for ri in active_right)

    # ---- 4. SSP（Dijkstra + 势）推到最大流 -------------------------------
    while True:
        dist = [INF] * n
        prev = [-1] * n
        dist[S] = 0
        pq: List[Tuple[int, int]] = [(0, S)]
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist[u]:
                continue
            for idx in adj[u]:
                fe = fedges[idx]
                if fe.cap <= 0:
                    continue
                nd = d + fe.cost + pi[u] - pi[fe.v]
                if nd < dist[fe.v]:
                    dist[fe.v] = nd
                    prev[fe.v] = idx
                    heapq.heappush(pq, (nd, fe.v))
        if dist[T] == INF:
            break
        for v in range(n):
            if dist[v] < INF:
                pi[v] += dist[v]
        v = T
        while v != S:
            idx = prev[v]
            fedges[idx].cap -= 1
            fedges[idx ^ 1].cap += 1
            v = fedges[idx].u

    # ---- 5. 读出被选中的中间边 -------------------------------------------
    result: List[Pair] = []
    for ei, fid in enumerate(mid):
        if fedges[fid].cap == 0:  # 容量耗尽 => 该匹配边被选中
            e = edges[ei]
            x, y = left[e.li].record_id, right[e.ri].record_id
            a, b = (x, y) if x <= y else (y, x)
            result.append(Pair(a=a, b=b, time_diff_micros=e.dt))
    return result
