# 应急无线电演练 · 通联日志互证服务

为应急无线电演练提供台站通联日志（QSO Log）的**相互印证配对**服务。
各台站把日志上报到本服务，服务在全场范围内自动找出互相指认的通联对，
并输出配对结果与未配对记录。

- 技术栈：Python 3.12 · FastAPI · SQLite（WAL）· Uvicorn
- 零外部依赖：SQLite 内嵌、数据文件持久化到 Docker 卷
- 自带 OpenAPI 交互文档：`/docs`、`/redoc`

---

## 1. 一键启动（Docker Compose）

```bash
docker compose up -d --build
```

服务启动时自动完成**数据初始化**（创建 SQLite 库、全部表、索引、WAL
配置），无需手工建库。初始化脚本为 `scripts/init_db.sh`，容器每次启动
都会幂等执行一次。

查看状态 / 日志 / 停止：

```bash
docker compose ps
docker compose logs -f
docker compose down            # 停止并删除容器（数据卷保留）
docker compose down -v         # 同时删除数据卷（清空演练数据）
```

## 2. 本地直接运行（不用 Docker）

```bash
pip install -r requirements.txt
export QSO_DATABASE=./data/qso.db     # SQLite 文件路径（默认 /data/qso.db）
python scripts/init_db.sh             # 可选：显式初始化数据库
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 3. 服务地址与端口

| 项目 | 值 |
| --- | --- |
| 服务地址 | `http://localhost:8000` |
| 健康检查 | `GET http://localhost:8000/health` |
| API 前缀 | `/api/v1` |
| Swagger UI | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |

## 4. 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QSO_DATABASE` | `/data/qso.db` | SQLite 数据库文件路径；目录不存在会自动创建 |
| `QSO_PORT` | `8000` | 容器监听端口 |
| Compose 端口映射 | `8000:8000` | 修改 `docker-compose.yml` 的 `ports` 即可换宿主端口，如 `18000:8000` |

---

## 5. 配对规则（业务语义）

一条日志包含：演练号、唯一记录号、本台呼号、对方呼号、**带时区**的
通联时间、波段、模式。

两条日志 `X`、`Y` 可以配对，当且仅当：

1. **相互指认**：`X.本台 == Y.对方` 且 `X.对方 == Y.本台`；
2. **波段相同、模式相同**（大小写、首尾空白不敏感）；
3. **时差不超限**：`|X.time - Y.time| <= 演练允许时差`（边界相等允许）；
4. 每条日志**最多参与一对**。

全场求解按以下目标字典序优化：

1. **最大化配对数**；
2. 在配对数最大的所有解中，**最小化总时差**（各对时差绝对值之和）；
3. 仍并列时，把每对的两个记录号升序排列成 `(a,b)`（`a < b`），再把
   所有配对按 `(a,b)` 整体升序，取**字典序最小**的配对列表。

### 结构与算法

按“无序呼号对 + 波段 + 模式”分组后，每组天然是一个二分图（左侧为本
台呼号较小的日志、右侧为本台呼号较大的日志），组间不共享任何记录，
相互独立。每组用**最小费用最大流**（SSP + Dijkstra 势，纯 Python 实现，
见 `app/matching.py`）一次解出：先把流量推到最大（对应目标 1），同流量
下取最小费用（对应目标 2、3）。

字典序目标被精确编码进整数费用，无浮点误差：

```
一条可行边的费用 = W * |Δt微秒| - 2^(E-1-rank)
W = 2^E，E = 本组可行边数
rank = 规范化配对 (min记录号, max记录号) 在所有可行边中的字典序排名
```

- `W` 大于时差项的任何可能差异，因此时差永远优先于字典序；
- 位权满足“高一位权大于所有低位权之和”，故费用最小的解等价于配对
  列表字典序最小的解（与“最大化配对数”组合时仍成立：SSP 先保证流
  量最大，再在最大流残量结构上取最小费用）。

该实现已对 **5000+ 个随机二分图与暴力枚举结果逐一比对一致**
（`tests/test_matching.py`）。

## 6. 版本与并发语义

- 演练创建后 `version = 0`；
- **新记录号**上报：插入并 `version += 1`，同一事务内重算全场配对；
- **同记录号、同内容重传**（内容为呼号/时间/波段/模式规范化后的
  SHA-256 指纹；时间按 UTC 规范化，`+08:00` 与等价的 `Z` 写法视为同一
  内容）：幂等，**不推进版本、不重算**；
- **同记录号、异内容**：视为**更正**，更新后 `version += 1` 并重算全场；
- **封存**：封存后拒绝任何写入（`409 DRILL_SEALED`），仍可查询最终
  配对结果与日志；重复封存幂等。封存本身不改变日志集，故不推进版本。
- 并发写入：进程内写锁 + SQLite `BEGIN IMMEDIATE` 保留锁串行化所有写
  事务；**版本号递增与配对结果替换在同一个事务里提交**，任何外部读
  到的状态都保证 `(version, pairs)` 自洽（要么旧版本旧配对，要么新版
  本新配对），不可能读到“版本已加、配对未更新”的中间态。WAL 模式下
  查询不阻塞写入。

---

## 7. HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/api/v1/drills` | 创建演练（演练号 + 非负允许时差秒） |
| GET | `/api/v1/drills` | 演练列表 |
| GET | `/api/v1/drills/{id}` | 演练详情（版本/封存/日志数/配对数） |
| POST | `/api/v1/drills/{id}/logs` | 上报/重传/更正单条日志 |
| PUT | `/api/v1/drills/{id}/logs/{record_id}` | 同上（幂等 upsert 语义） |
| POST | `/api/v1/drills/{id}/logs/batch` | 批量上报（整批原子校验+提交） |
| GET | `/api/v1/drills/{id}/logs` | 该演练全部日志 |
| GET | `/api/v1/drills/{id}/result` | 最终/当前互证结果（配对 + 未配对） |
| POST | `/api/v1/drills/{id}/seal` | 封存演练 |

### 7.1 创建演练

```bash
curl -sS -X POST http://localhost:8000/api/v1/drills \
  -H 'Content-Type: application/json' \
  -d '{"drill_id": "EX-2026", "allowed_time_diff_seconds": 60}'
```

`allowed_time_diff_seconds` 接受非负整数、小数或数字字符串（秒）。
重复演练号返回 `409 DRILL_EXISTS`。

### 7.2 上报日志

```bash
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
```

- `timestamp` 必须是 **ISO 8601 且带时区**（`+08:00`、`Z` 均可），服务
  统一换算成 UTC 存储与比对；
- 呼号、波段、模式自动 `strip + upper` 规范化；
- 响应直接带回提交后的全场结果（`version`、`pairs`、`unpaired`、
  `retransmitted` / `corrected` 标志）。

### 7.3 批量上报

```bash
curl -sS -X POST http://localhost:8000/api/v1/drills/EX-2026/logs/batch \
  -H 'Content-Type: application/json' \
  -d '{"logs": [ { ... 日志1 ... }, { ... 日志2 ... } ]}'
```

整批先做全部校验（任意一条非法则整批拒绝，错误体 `details.index` 指明
下标），通过后在一个事务内完成所有 upsert 与全场重算；一批新记录/更
正只让版本前进 **1**。

### 7.4 查询互证结果

```bash
curl -sS http://localhost:8000/api/v1/drills/EX-2026/result
```

响应（节选）：

```json
{
  "drill_id": "EX-2026",
  "version": 2,
  "sealed": false,
  "allowed_time_diff_seconds": 60.0,
  "pair_count": 1,
  "unpaired_count": 1,
  "total_time_diff_seconds": 40.0,
  "pairs": [
    {
      "a": "LOG-001", "b": "LOG-002",
      "time_diff_seconds": 40.0, "time_diff_micros": 40000000,
      "band": "20M", "mode": "SSB",
      "home_a": "BH1AAA", "peer_a": "BH2BBB", "timestamp_a": "2026-10-04T02:00:00Z",
      "home_b": "BH2BBB", "peer_b": "BH1AAA", "timestamp_b": "2026-10-04T02:00:40Z"
    }
  ],
  "unpaired": [ { "record_id": "LOG-003", "...": "..." } ]
}
```

`pairs` 按 `(a,b)` 升序，`unpaired` 按记录号升序。

### 7.5 封存

```bash
curl -sS -X POST http://localhost:8000/api/v1/drills/EX-2026/seal
```

之后日志写入返回 `409 DRILL_SEALED`；`GET .../result`、`GET .../logs`
照常返回最终结果。

完整可复制请求见 [`examples/requests.sh`](examples/requests.sh)。

## 8. 错误响应与状态码

统一响应体：`{"error": {"code": "...", "message": "...", "details": ...}}`

| HTTP | code | 触发场景 |
| --- | --- | --- |
| 400 | `INVALID_TIME_TOLERANCE` | 时差为负、NaN/Infinity、布尔或非数字 |
| 400 | `INVALID_TIMESTAMP` | 时间字符串无法解析 |
| 400 | `TIMESTAMP_WITHOUT_TIMEZONE` | 时间不带时区信息 |
| 400 | `SAME_CALLSIGN` | 本台呼号与对方呼号相同 |
| 400 | `INVALID_CALLSIGN` / `INVALID_BAND_MODE` / `INVALID_RECORD_ID` / `INVALID_DRILL_ID` | 对应字段为空或类型错误 |
| 400 | `RECORD_ID_MISMATCH` | PUT 路径记录号与请求体不一致 |
| 400 | `BATCH_RECORD_CONFLICT` | 同一批次内同记录号内容冲突 |
| 404 | `DRILL_NOT_FOUND` | 向未知演练写日志 / 查询 |
| 409 | `DRILL_EXISTS` | 演练号已存在 |
| 409 | `DRILL_SEALED` | 演练已封存仍尝试写入 |
| 422 | `VALIDATION_ERROR` | 请求体结构层面校验失败（缺字段/多余字段/类型不符） |

## 9. 测试

```bash
python tests/test_matching.py   # 匹配引擎：规则场景 + 800 随机图暴力对拍
python tests/test_api.py        # API 全流程：38 项断言，含 40 线程并发写入、封存
```

（如安装了 pytest，也可直接 `pytest tests/`。）

## 10. 项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由与生命周期
│   ├── services.py      # 业务逻辑：创建/上报/重传/更正/封存/结果构建
│   ├── storage.py       # SQLite 连接、事务、写锁、建表初始化
│   ├── matching.py      # 二分图最小费用最大流互证引擎
│   ├── validation.py    # 时差/时间/呼号规范化与内容指纹
│   ├── schemas.py       # 请求/响应模型
│   └── errors.py        # 统一错误处理
├── scripts/
│   ├── init_db.sh       # 数据初始化（幂等）
│   └── entrypoint.sh    # 容器入口（初始化 + uvicorn）
├── tests/               # 引擎与 API 测试
├── examples/requests.sh # 全部 curl 示例
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
