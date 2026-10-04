"""端到端 API 测试。

直接运行：python tests/test_api.py
（不依赖 pytest；如装有 pytest 也可 pytest 直接收集。）
"""

from __future__ import annotations

import os
import sys
import threading

os.environ["QSO_DATABASE"] = ":memory:"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

def main():
    with TestClient(app) as client:
        globals()["client"] = client
        run()


client = None

_passed = 0


def check(name: str, cond: bool, detail=""):
    global _passed
    assert cond, f"{name} FAILED: {detail}"
    _passed += 1
    print(f"  ok - {name}")


def test_health():
    r = client.get("/health")
    check("health 200", r.status_code == 200, r.text)


def run():
    test_health()

    # ---- 非法时差 / 演练号 ----------------------------------------------
    r = client.post("/api/v1/drills", json={"drill_id": "EX1", "allowed_time_diff_seconds": -1})
    check("负时差 400", r.status_code == 400 and r.json()["error"]["code"] == "INVALID_TIME_TOLERANCE", r.text)

    r = client.post("/api/v1/drills", json={"drill_id": "EX1", "allowed_time_diff_seconds": "abc"})
    check("非数字时差 400", r.status_code == 400, r.text)

    r = client.post("/api/v1/drills", json={"drill_id": "EX1", "allowed_time_diff_seconds": True})
    check("布尔时差 400", r.status_code == 400, r.text)

    r = client.post("/api/v1/drills", json={"drill_id": "", "allowed_time_diff_seconds": 60})
    check("空演练号 400", r.status_code == 400 and r.json()["error"]["code"] == "INVALID_DRILL_ID", r.text)

    # ---- 建演练 ----------------------------------------------------------
    r = client.post("/api/v1/drills", json={"drill_id": "D2026", "allowed_time_diff_seconds": 60})
    check("建演练 201", r.status_code == 201 and r.json()["version"] == 0, r.text)
    drill_url = "/api/v1/drills/D2026"

    r = client.post("/api/v1/drills", json={"drill_id": "D2026", "allowed_time_diff_seconds": 60})
    check("重复演练 409", r.status_code == 409 and r.json()["error"]["code"] == "DRILL_EXISTS", r.text)

    r = client.get(drill_url)
    check("查询演练", r.status_code == 200 and r.json()["log_count"] == 0, r.text)

    # ---- 未知演练 --------------------------------------------------------
    r = client.get("/api/v1/drills/NOPE/result")
    check("未知演练 404", r.status_code == 404 and r.json()["error"]["code"] == "DRILL_NOT_FOUND", r.text)

    # ---- 非法日志 --------------------------------------------------------
    base_log = {
        "record_id": "r1",
        "home_callsign": "BH1AAA",
        "peer_callsign": "BH2BBB",
        "timestamp": "2026-10-04T10:00:00+08:00",
        "band": "20m",
        "mode": "ssb",
    }
    bad = dict(base_log, timestamp="2026-10-04T10:00:00")
    r = client.post(f"{drill_url}/logs", json=bad)
    check("无时区时间 400", r.status_code == 400 and r.json()["error"]["code"] == "TIMESTAMP_WITHOUT_TIMEZONE", r.text)

    bad = dict(base_log, timestamp="not-a-time")
    r = client.post(f"{drill_url}/logs", json=bad)
    check("坏时间格式 400", r.status_code == 400 and r.json()["error"]["code"] == "INVALID_TIMESTAMP", r.text)

    bad = dict(base_log, peer_callsign="bh1aaa")
    r = client.post(f"{drill_url}/logs", json=bad)
    check("本台=对方 400", r.status_code == 400 and r.json()["error"]["code"] == "SAME_CALLSIGN", r.text)

    r = client.post("/api/v1/drills/GHOST/logs", json=base_log)
    check("向未知演练写日志 404", r.status_code == 404, r.text)

    # ---- 正常互证全流程 --------------------------------------------------
    l1 = dict(base_log)
    l2 = {
        "record_id": "r2",
        "home_callsign": "BH2BBB",
        "peer_callsign": "BH1AAA",
        "timestamp": "2026-10-04T10:00:30+08:00",
        "band": "20M",
        "mode": "SSB",
    }
    r = client.post(f"{drill_url}/logs", json=l1)
    body = r.json()
    check("首条日志 version=1, 未配对", r.status_code == 200 and body["version"] == 1 and body["pair_count"] == 0 and body["unpaired_count"] == 1, r.text)

    r = client.post(f"{drill_url}/logs", json=l2)
    body = r.json()
    check("第二条互证成功 version=2", r.status_code == 200 and body["version"] == 2 and body["pair_count"] == 1, r.text)
    pair = body["pairs"][0]
    check("配对内容", (pair["a"], pair["b"]) == ("r1", "r2") and pair["time_diff_seconds"] == 30.0, pair)
    check("未配对列表为空", body["unpaired"] == [], body)

    # ---- 同内容重传：不推进版本 ------------------------------------------
    r = client.post(f"{drill_url}/logs", json=l2)
    body = r.json()
    check("重传幂等 version 仍 2", body["version"] == 2 and body["retransmitted"] is True, r.text)

    # 时区写法不同但同一时刻也视为同内容（规范化后比较）
    l2z = dict(l2, timestamp="2026-10-04T02:00:30Z")
    r = client.post(f"{drill_url}/logs", json=l2z)
    check("等价时区重传仍幂等", r.json()["version"] == 2 and r.json()["retransmitted"] is True, r.text)

    # ---- 异内容更正：重算全场，version+1 --------------------------------
    l2fix = dict(l2, timestamp="2026-10-04T10:00:45+08:00")
    r = client.post(f"{drill_url}/logs", json=l2fix)
    body = r.json()
    check("更正后 version=3", body["version"] == 3 and body["corrected"] is True, r.text)
    check("更正后时差 45s", body["pairs"][0]["time_diff_seconds"] == 45.0, r.text)

    # 更正后导致时差超限 -> 配对破裂
    l2far = dict(l2, timestamp="2026-10-04T10:05:00+08:00")
    r = client.post(f"{drill_url}/logs", json=l2far)
    body = r.json()
    check("超时差更正 version=4 配对破裂", body["version"] == 4 and body["pair_count"] == 0 and body["unpaired_count"] == 2, r.text)

    # ---- 结果查询 --------------------------------------------------------
    r = client.get(f"{drill_url}/result")
    body = r.json()
    check("结果查询", body["version"] == 4 and body["pair_count"] == 0 and len(body["unpaired"]) == 2, r.text)

    # ---- 批量提交 --------------------------------------------------------
    r = client.post(
        "/api/v1/drills/D2026/logs/batch",
        json={"logs": [dict(l2, timestamp="2026-10-04T10:00:20+08:00")]},
    )
    body = r.json()
    check("批量更正恢复配对 version=5", body["version"] == 5 and body["pair_count"] == 1, r.text)
    check("批量统计", body["accepted"] == 1 and body["corrected"] == 1 and body["retransmitted"] == 0, r.text)

    r = client.post(
        "/api/v1/drills/D2026/logs/batch",
        json={"logs": [
            dict(l2, timestamp="2026-10-04T10:00:20+08:00"),
            dict(l1),
        ]},
    )
    body = r.json()
    check("批量全重传 version 不变", body["version"] == 5 and body["retransmitted"] == 2, r.text)

    r = client.post("/api/v1/drills/D2026/logs/batch", json={"logs": [dict(l1, timestamp="2026-10-04T10:00:00")]})
    check("批量单条非法 400 且带 index", r.status_code == 400 and r.json()["error"]["details"]["index"] == 0, r.text)
    check("批量非法不推进版本", client.get("/api/v1/drills/D2026").json()["version"] == 5)

    # PUT 路径记录号不一致
    r = client.put("/api/v1/drills/D2026/logs/r9", json=l1)
    check("PUT 记录号不一致 400", r.status_code == 400 and r.json()["error"]["code"] == "RECORD_ID_MISMATCH", r.text)

    # ---- 封存 ------------------------------------------------------------
    r = client.post(f"{drill_url}/seal")
    check("封存 200", r.status_code == 200 and r.json()["sealed"] is True, r.text)
    r = client.post(f"{drill_url}/seal")
    check("重复封存幂等", r.status_code == 200 and r.json()["sealed"] is True, r.text)

    r = client.post(f"{drill_url}/logs", json=dict(base_log, record_id="r3"))
    check("封存后拒绝写入 409", r.status_code == 409 and r.json()["error"]["code"] == "DRILL_SEALED", r.text)

    r = client.get(f"{drill_url}/result")
    check("封存后仍可查询最终结果", r.status_code == 200 and r.json()["sealed"] is True and r.json()["version"] == 5, r.text)

    r = client.get(f"{drill_url}/logs")
    check("封存后仍可查询日志", r.status_code == 200 and len(r.json()) == 2, r.text)

    # ---- 字典序 tie-break 端到端 ----------------------------------------
    client.post("/api/v1/drills", json={"drill_id": "TIE", "allowed_time_diff_seconds": 10})
    url = "/api/v1/drills/TIE/logs/batch"
    logs = [
        {"record_id": "1", "home_callsign": "A", "peer_callsign": "B", "timestamp": "2026-01-01T00:00:00Z", "band": "20M", "mode": "SSB"},
        {"record_id": "2", "home_callsign": "A", "peer_callsign": "B", "timestamp": "2026-01-01T00:00:00Z", "band": "20M", "mode": "SSB"},
        {"record_id": "4", "home_callsign": "B", "peer_callsign": "A", "timestamp": "2026-01-01T00:00:00Z", "band": "20M", "mode": "SSB"},
        {"record_id": "3", "home_callsign": "B", "peer_callsign": "A", "timestamp": "2026-01-01T00:00:00Z", "band": "20M", "mode": "SSB"},
    ]
    r = client.post(url, json={"logs": logs})
    pairs = sorted((p["a"], p["b"]) for p in r.json()["pairs"])
    check("字典序 tie-break [(1,3),(2,4)]", pairs == [("1", "3"), ("2", "4")], pairs)

    # ---- 并发写入原子性 --------------------------------------------------
    client.post("/api/v1/drills", json={"drill_id": "CONC", "allowed_time_diff_seconds": 3600})

    def make(i):
        return {
            "record_id": f"c{i:03d}",
            "home_callsign": "A",
            "peer_callsign": "B",
            "timestamp": f"2026-01-01T00:00:00Z",
            "band": "20M",
            "mode": "SSB",
        }

    errors = []

    def worker(start):
        try:
            for i in range(start, start + 10):
                rr = client.post("/api/v1/drills/CONC/logs", json=make(i))
                if rr.status_code != 200:
                    errors.append(rr.text)
        except Exception as e:  # noqa
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(s,)) for s in (0, 10, 20, 30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("40 条并发写入无错误", not errors, errors[:3])
    body = client.get("/api/v1/drills/CONC/result").json()
    check("并发后版本=40、40 条记录全在", body["version"] == 40 and body["unpaired_count"] == 40 and body["pair_count"] == 0,
          (body["version"], body["unpaired_count"], body["pair_count"]))

    # version 与配对始终自洽（配对数恒为偶数关系不适用，但版本=40 保证每写一条都原子提交）
    drill = client.get("/api/v1/drills/CONC").json()
    check("演练元信息 log_count=40", drill["log_count"] == 40, drill)

    print(f"\nALL {_passed} CHECKS PASSED")


if __name__ == "__main__":
    main()
