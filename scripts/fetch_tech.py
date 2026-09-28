"""技术岗抓取：从社区聚合源拉取校招岗位 → jobs.db 的 tech_jobs 表。

设计要点：
  - 只读公开的 GitHub 聚合 JSON，不爬任何招聘平台，无封号风险
  - 每个源先存原始快照到 data/raw/YYYY-MM-DD/，便于回溯与源失效时迁移
  - 按 job_id 增量入库：新记录写 first_seen，老记录只刷新 last_seen

用法：
    python scripts/fetch_tech.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    connect, fix_stdout, http_get_json, load_config, make_id, save_snapshot, upsert_jobs,
)


# ---------------------------------------------------------------- 解析器
# 每个解析器把一种源的原始结构归一化成 tech_jobs 的字段。


def parse_radar(payload: dict, source: str) -> list[dict]:
    """校招雷达格式：{updated, count, jobs: [{c,p,l,w,d,ind,u,...}]}

    字段是单字母缩写：c=公司 p=岗位 l=地点 w=批次 d=截止 s=来源 t=类型
                     ind=行业 u=投递链接 e=学历
    """
    rows: list[dict] = []
    for item in payload.get("jobs") or []:
        company = (item.get("c") or "").strip()
        position = (item.get("p") or "").strip()
        if not company:
            continue

        # "批次:27届秋招正式批" -> "27届秋招正式批"
        batch = (item.get("w") or "").strip()
        if ":" in batch or "：" in batch:
            batch = batch.split(":", 1)[-1].split("：", 1)[-1].strip()

        rows.append({
            "job_id": make_id(company, batch, position),
            "company": company,
            "position": position,
            "locations": (item.get("l") or "").strip(),
            "batch": batch,
            "cohort": "",
            "industry": (item.get("ind") or item.get("t") or "").strip(),
            "apply_url": (item.get("u") or "").strip(),
            "deadline": (item.get("d") or "").strip(),
            "source": source,
        })
    return rows


def parse_xixicc(payload: list, source: str) -> list[dict]:
    """xixicc2027 格式：[{company, cohort, batch, industry, positions[],
    locations[], apply_url, official_wechat, deadline, ...}]
    """
    rows: list[dict] = []
    for item in payload or []:
        company = (item.get("company") or "").strip()
        if not company:
            continue

        positions = item.get("positions") or []
        position = "、".join(str(p).strip() for p in positions if str(p).strip())

        locations = item.get("locations") or []
        location = "、".join(str(x).strip() for x in locations if str(x).strip())

        # apply_url 可能为空，退而用官方公众号作为投递线索
        apply_url = (item.get("apply_url") or "").strip()
        wechat = (item.get("official_wechat") or "").strip()
        if not apply_url and wechat:
            apply_url = f"微信公众号：{wechat}"

        batch = (item.get("batch") or "").strip()
        cohort = (item.get("cohort") or "").strip()

        rows.append({
            "job_id": make_id(company, f"{cohort}{batch}", position),
            "company": company,
            "position": position,
            "locations": location,
            "batch": f"{cohort} {batch}".strip(),
            "cohort": cohort,
            "industry": (item.get("industry") or "").strip(),
            "apply_url": apply_url,
            "deadline": (item.get("deadline") or "").strip(),
            "source": source,
        })
    return rows


PARSERS = {
    "radar": parse_radar,
    "xixicc": parse_xixicc,
}


# ---------------------------------------------------------------- 主流程


def main() -> int:
    fix_stdout()
    cfg = load_config()
    conn = connect()

    total_new = total_upd = 0
    failures: list[str] = []

    for src in cfg["sources"]["tech"]:
        name = src["name"]
        print(f"[{name}] 拉取 {src['url']}")

        try:
            payload = http_get_json(src["url"], timeout=45)
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] [X] 失败：{exc}")
            failures.append(name)
            continue

        save_snapshot(name, payload)

        parser = PARSERS.get(src["format"])
        if parser is None:
            print(f"[{name}] [X] 未知格式 {src['format']}")
            failures.append(name)
            continue

        rows = parser(payload, name)
        new, upd = upsert_jobs(conn, "tech_jobs", rows)
        total_new += new
        total_upd += upd
        print(f"[{name}] [OK] 解析 {len(rows)} 条 -> 新增 {new}，更新 {upd}")

    count = conn.execute("SELECT COUNT(*) FROM tech_jobs").fetchone()[0]
    print(f"\n完成：本次新增 {total_new}，更新 {total_upd}；tech_jobs 共 {count} 条")
    if failures:
        print(f"失败源：{', '.join(failures)}（不影响其他源）")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
