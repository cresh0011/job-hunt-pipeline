"""job-hunt-2027 共享工具：路径、数据库、HTTP、快照。

本模块只依赖 Python 标准库（pyyaml 除外，用于读配置）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 路径

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "jobs.db"
CONFIG_PATH = ROOT / "config.yaml"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fix_stdout() -> None:
    """Windows 控制台默认 GBK，中文输出会乱码 —— 统一切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def today() -> str:
    return date.today().isoformat()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config() -> dict:
    import yaml

    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- 数据库

SCHEMA = """
PRAGMA journal_mode = WAL;

-- 技术岗（互联网/AI/芯片/机器人）—— 与国央企严格分离
CREATE TABLE IF NOT EXISTS tech_jobs (
    job_id      TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    position    TEXT,
    locations   TEXT,
    batch       TEXT,
    cohort      TEXT,
    industry    TEXT,
    apply_url   TEXT,
    deadline    TEXT,
    source      TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    match_score REAL DEFAULT 0,
    status      TEXT DEFAULT '待投递'
);

-- 国央企（国聘网 + 国家大学生就业服务平台）
CREATE TABLE IF NOT EXISTS soe_jobs (
    job_id       TEXT PRIMARY KEY,
    company      TEXT NOT NULL,
    job_name     TEXT,
    nature_cn    TEXT,
    education_cn TEXT,
    district_cn  TEXT,
    amount       INTEGER,
    wage         TEXT,
    end_time     TEXT,
    apply_url    TEXT,
    majors       TEXT,          -- 专业要求（国聘 major_cn）
    jd           TEXT,          -- 完整岗位描述（国聘 contents）
    source       TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    match_score  REAL DEFAULT 0,
    status       TEXT DEFAULT '待投递'
);

-- 投递事件日志（只追加，不修改）—— 支撑进度时间线
CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    track      TEXT NOT NULL,          -- 'tech' | 'soe'
    status     TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    notes      TEXT,
    UNIQUE (job_id, track, status)
);

CREATE INDEX IF NOT EXISTS idx_tech_score ON tech_jobs (match_score DESC);
CREATE INDEX IF NOT EXISTS idx_soe_end    ON soe_jobs (end_time);
CREATE INDEX IF NOT EXISTS idx_app_job    ON applications (job_id, track);
"""


# 后加的列。SQLite 没有 ADD COLUMN IF NOT EXISTS，用 PRAGMA 探测后补。
_EXTRA_COLUMNS: dict[str, dict[str, str]] = {
    "tech_jobs": {"cohort": "TEXT"},
    "soe_jobs": {"majors": "TEXT", "jd": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    """给已存在的表补上后加的列，避免改表结构就得重建整库。"""
    for table, cols in _EXTRA_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, coltype in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    conn.commit()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# ---------------------------------------------------------------- 去重

# 归一化时抹掉的字符：空白、常见中英文标点、分隔符
_PUNCT = re.compile(r"[\s　·、,，。.；;：:!！?？()（）\[\]【】{}<>\"'“”‘’/\\|~—\-_+*#]+")


def norm(text: object) -> str:
    """把文本归一化到可比较的形式，用于生成稳定的去重 key。"""
    return _PUNCT.sub("", str(text or "").strip().lower())


def make_id(*parts: object) -> str:
    """由若干字段生成稳定的 16 位 job_id。字段顺序必须固定。"""
    key = "|".join(norm(p) for p in parts)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- HTTP


def _request(url: str, data: bytes | None = None, headers: dict | None = None,
             timeout: int = 30, retries: int = 3, backoff: float = 2.0):
    """带重试的 HTTP 请求，返回响应字节。低频、串行、指数退避。"""
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    hdrs.update(headers or {})

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 — 网络异常种类多，统一重试
            last_err = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def http_get_json(url: str, headers: dict | None = None, timeout: int = 30):
    return json.loads(_request(url, headers=headers, timeout=timeout).decode("utf-8"))


def http_post_json(url: str, payload: dict, headers: dict | None = None,
                   timeout: int = 30):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    body = json.dumps(payload).encode("utf-8")
    return json.loads(_request(url, data=body, headers=hdrs, timeout=timeout).decode("utf-8"))


# ---------------------------------------------------------------- 快照


def save_snapshot(source: str, payload) -> Path:
    """把原始响应留档到 data/raw/YYYY-MM-DD/<source>.json，便于回溯与源失效时迁移。"""
    day_dir = RAW_DIR / today()
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{source}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path


# ---------------------------------------------------------------- 入库


def upsert_jobs(conn: sqlite3.Connection, table: str, rows: list[dict]) -> tuple[int, int]:
    """按 job_id 增量入库。

    已存在 -> 只更新 last_seen 与可变字段，保留 first_seen / status / match_score。
    新记录 -> 写入并打上 first_seen。

    返回 (新增数, 更新数)。
    """
    stamp = now()
    inserted = updated = 0

    for row in rows:
        job_id = row.get("job_id")
        if not job_id:
            continue

        exists = conn.execute(
            f"SELECT 1 FROM {table} WHERE job_id = ?", (job_id,)
        ).fetchone()

        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        values = [row[c] for c in cols]

        if exists:
            # 不覆盖 first_seen / status / match_score
            mutable = [c for c in cols if c not in ("job_id", "first_seen")]
            assignments = ", ".join(f"{c} = ?" for c in mutable)
            conn.execute(
                f"UPDATE {table} SET {assignments}, last_seen = ? WHERE job_id = ?",
                [row[c] for c in mutable] + [stamp, job_id],
            )
            updated += 1
        else:
            conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}, first_seen, last_seen) "
                f"VALUES ({placeholders}, ?, ?)",
                values + [stamp, stamp],
            )
            inserted += 1

    conn.commit()
    return inserted, updated


def log_event(conn: sqlite3.Connection, job_id: str, track: str, status: str,
              notes: str = "") -> None:
    """追加一条投递状态事件。UNIQUE 约束保证同一状态不重复记录。"""
    conn.execute(
        "INSERT OR IGNORE INTO applications (job_id, track, status, changed_at, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (job_id, track, status, now(), notes),
    )
    conn.commit()


def days_until(date_str: str | None) -> int | None:
    """距离给定日期还有几天；无法解析返回 None。"""
    if not date_str:
        return None
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", str(date_str))
    if not m:
        return None
    try:
        target = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (target - date.today()).days


def is_recent(date_str: str | None, days: int) -> bool:
    """判断时间戳是否在最近 N 天内。"""
    if not date_str:
        return False
    try:
        ts = datetime.strptime(str(date_str)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            ts = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        except ValueError:
            return False
    return datetime.now() - ts <= timedelta(days=days)
