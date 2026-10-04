# 应急无线电演练 · 通联日志互证服务请求示例
#
# 服务默认地址: http://localhost:8000
# 交互式文档:   http://localhost:8000/docs  (Swagger UI)
#               http://localhost:8000/redoc (ReDoc)
#
# 所有接口前缀: /api/v1

## 1. 创建演练（配置非负允许时差，单位：秒）
curl -sS -X POST http://localhost:8000/api/v1/drills \
  -H 'Content-Type: application/json' \
  -d '{
    "drill_id": "EX-2026",
    "allowed_time_diff_seconds": 60
  }'

## 2. 提交单条台站日志（时间必须带时区）
curl -sS -X POST http://localhost:8000/api/v1/drills/EX-2026/logs \
  -H 'Content-Type: application/json' \
  -d '{
    "record_id": "LOG-001",
    "home_callsign": "BH1AAA",
    "peer_callsign": "BH2BBB",
    "timestamp": "2026-10-04T10:00:00+08:00",
    "band": "20m",
    "mode": "ssb"
  }'

# PUT 形式（路径记录号必须与请求体一致，否则 400）
curl -sS -X PUT http://localhost:8000/api/v1/drills/EX-2026/logs/LOG-001 \
  -H 'Content-Type: application/json' \
  -d '{
    "record_id": "LOG-001",
    "home_callsign": "BH1AAA",
    "peer_callsign": "BH2BBB",
    "timestamp": "2026-10-04T10:00:00+08:00",
    "band": "20M",
    "mode": "SSB"
  }'

## 3. 批量提交（原子：任一非法整批拒绝）
curl -sS -X POST http://localhost:8000/api/v1/drills/EX-2026/logs/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "logs": [
      {"record_id":"LOG-001","home_callsign":"BH1AAA","peer_callsign":"BH2BBB",
       "timestamp":"2026-10-04T10:00:00+08:00","band":"20M","mode":"SSB"},
      {"record_id":"LOG-002","home_callsign":"BH2BBB","peer_callsign":"BH1AAA",
       "timestamp":"2026-10-04T10:00:40+08:00","band":"20M","mode":"SSB"}
    ]
  }'

## 4. 查询互证结果（配对 + 未配对记录）
curl -sS http://localhost:8000/api/v1/drills/EX-2026/result

## 5. 查询全部日志 / 演练信息
curl -sS http://localhost:8000/api/v1/drills/EX-2026/logs
curl -sS http://localhost:8000/api/v1/drills/EX-2026
curl -sS http://localhost:8000/api/v1/drills

## 6. 封存演练（封存后写入返回 409，仍可查询最终结果；重复封存幂等）
curl -sS -X POST http://localhost:8000/api/v1/drills/EX-2026/seal

## 7. 健康检查
curl -sS http://localhost:8000/health

## 错误响应示例
# 非法时差:   400 INVALID_TIME_TOLERANCE
curl -sS -X POST http://localhost:8000/api/v1/drills \
  -H 'Content-Type: application/json' \
  -d '{"drill_id":"BAD","allowed_time_diff_seconds":-3}'

# 无时区时间: 400 TIMESTAMP_WITHOUT_TIMEZONE
curl -sS -X POST http://localhost:8000/api/v1/drills/EX-2026/logs \
  -H 'Content-Type: application/json' \
  -d '{"record_id":"X","home_callsign":"A","peer_callsign":"B",
       "timestamp":"2026-10-04T10:00:00","band":"20M","mode":"SSB"}'

# 本台=对方:  400 SAME_CALLSIGN
# 未知演练:   404 DRILL_NOT_FOUND
# 演练已存在: 409 DRILL_EXISTS
# 封存后写入: 409 DRILL_SEALED
