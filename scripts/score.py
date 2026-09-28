"""匹配打分：给 tech_jobs / soe_jobs 里的每条岗位算 match_score 并落库。

打分维度（权重全部来自 config.yaml，改配置即可，无需改代码）：
  1. 关键词   —— 岗位文本命中 config.keyword_weights / negative_keywords
  2. 城市     —— 上海优先（取命中的最高权重，不累加，避免"多地"占便宜）
  3. 行业     —— industry 字段
  4. 批次     —— 正职 vs 实习
  5. 学历     —— 本科是用户的实际学历，要求硕士的重罚
  6. 专业     —— 国聘的 major_cn 是否含计算机类
  7. 时效     —— 已过截止日期的技术岗降权

用法：
    python scripts/score.py            # 打分并打印 Top 榜
    python scripts/score.py --top 30   # 自定义展示条数
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import connect, days_until, fix_stdout, load_config  # noqa: E402


def _hit(text: str, table: dict[str, float]) -> tuple[float, list[str]]:
    """在 text 中查找 table 的键，返回 (累计权重, 命中的键)。

    每个键最多计一次 —— 同一关键词重复出现不重复加分。
    """
    if not text:
        return 0.0, []
    low = text.lower()
    total = 0.0
    hits: list[str] = []
    for key, weight in table.items():
        if str(key).lower() in low:
            total += weight
            hits.append(key)
    return total, hits


def _best(text: str, table: dict[str, float]) -> tuple[float, str]:
    """多地/多行业时取命中的最高权重，而非累加。"""
    score, hits = _hit(text, table)
    if not hits:
        return 0.0, ""
    best_key = max(hits, key=lambda k: table[k])
    return table[best_key], best_key


def score_tech(row, cfg: dict) -> tuple[float, list[str]]:
    """技术岗打分。"""
    text = " ".join(filter(None, [
        row["company"], row["position"], row["industry"], row["batch"],
    ]))

    total = 0.0
    reasons: list[str] = []

    s, hits = _hit(text, cfg["keyword_weights"])
    total += s
    reasons += hits[:6]

    s, hits = _hit(text, cfg["negative_keywords"])
    total += s
    if hits:
        reasons.append("(-)" + "/".join(hits[:4]))

    s, city = _best(row["locations"] or "", cfg["city_weights"])
    total += s
    if city:
        reasons.append(city)

    s, ind = _best(row["industry"] or "", cfg["industry_weights"])
    total += s
    if ind:
        reasons.append(ind)

    s, hits = _hit(row["batch"] or "", cfg["batch_rules"]["deprioritize"])
    total += s
    if hits:
        reasons.append("批次:" + "/".join(hits))

    # 已过截止日期 -> 降权（但仍保留，部分岗位会延后）
    left = days_until(row["deadline"])
    if left is not None and left < 0:
        total -= 25
        reasons.append(f"已截止{abs(left)}天")

    return total, reasons


# JD 正文里命中的关键词按此系数折算。正文越长越容易堆砌技术名词，
# 不折算会让「JD 写得长」的岗位无脑排前面。
_BODY_DISCOUNT = 0.4


def score_soe(row, cfg: dict) -> tuple[float, list[str]]:
    """国央企打分。

    岗位名 / 专业要求里的关键词按全权重计分，JD 正文里的折算 0.4。
    负向词只看岗位名 —— JD 里出现「运营」「财务」多是描述协作方，不是岗位本身。
    """
    title_text = " ".join(filter(None, [
        row["company"], row["job_name"], row["majors"],
    ]))
    body_text = row["jd"] or ""

    total = 0.0
    reasons: list[str] = []

    # 关键词：岗位名全权重
    title_score, title_hits = _hit(title_text, cfg["keyword_weights"])
    total += title_score
    reasons += title_hits[:5]

    # 关键词：JD 正文中「岗位名未命中」的部分打折计入
    _, body_hits = _hit(body_text, cfg["keyword_weights"])
    body_only = [h for h in body_hits if h not in title_hits]
    if body_only:
        total += _BODY_DISCOUNT * sum(cfg["keyword_weights"][h] for h in body_only)
        reasons.append("JD:" + "/".join(body_only[:4]))

    # 负向词：只看岗位名
    neg_score, neg_hits = _hit(title_text, cfg["negative_keywords"])
    total += neg_score
    if neg_hits:
        reasons.append("(-)" + "/".join(neg_hits[:4]))

    s, city = _best(row["district_cn"] or "", cfg["city_weights"])
    total += s
    if city:
        reasons.append(city)

    # 学历：用户是本科，要求硕士的岗位重罚。
    # 取最高权重而非累加 —— "本科及以上" 同时包含 "本科"，累加会重复计分。
    edu_score, edu_key = _best(row["education_cn"] or "", cfg["education_weights"])
    total += edu_score
    if edu_key:
        reasons.append(edu_key)

    # 专业要求是否含计算机类。同样取最高 —— "计算机类、计算机科学与技术、
    # 软件工程" 是同义列举，累加会让岗位凭空多出 58 分，把消防管理岗顶到前排。
    major_score, major_key = _best(row["majors"] or "", cfg["major_weights"])
    total += major_score
    if major_key:
        reasons.append(major_key)

    return total, reasons


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20, help="展示条数")
    args = ap.parse_args()

    fix_stdout()
    cfg = load_config()
    conn = connect()

    # --- 技术岗 ---
    tech = conn.execute("SELECT * FROM tech_jobs").fetchall()
    for row in tech:
        s, _ = score_tech(row, cfg)
        conn.execute("UPDATE tech_jobs SET match_score = ? WHERE job_id = ?", (s, row["job_id"]))
    conn.commit()

    # --- 国央企 ---
    soe = conn.execute("SELECT * FROM soe_jobs").fetchall()
    for row in soe:
        s, _ = score_soe(row, cfg)
        conn.execute("UPDATE soe_jobs SET match_score = ? WHERE job_id = ?", (s, row["job_id"]))
    conn.commit()

    print(f"已打分：tech_jobs {len(tech)} 条，soe_jobs {len(soe)} 条\n")

    def show(table: str, scorer, title: str, extra_col: str) -> None:
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY match_score DESC LIMIT ?", (args.top,)
        ).fetchall()
        print("=" * 78)
        print(f"  {title} — Top {len(rows)}")
        print("=" * 78)
        for i, row in enumerate(rows, 1):
            s, reasons = scorer(row, cfg)
            dl = row["deadline"] if "deadline" in row.keys() else row["end_time"]
            left = days_until(dl)
            urgent = ""
            if left is not None:
                urgent = f" | 截止{left}天" if left >= 0 else f" | 已截止{abs(left)}天"
            print(f"{i:>3}. [{s:>6.1f}] {row['company'][:22]} — {extra_col and row[extra_col][:38]}")
            print(f"       {row['district_cn'] if 'district_cn' in row.keys() else row['locations']}"
                  f"{urgent}  ‹{', '.join(reasons[:7])}›")
        print()

    show("tech_jobs", score_tech, "技术岗", "position")
    show("soe_jobs", score_soe, "国央企", "job_name")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
