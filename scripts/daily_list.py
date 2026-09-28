"""生成今天的投递清单 —— 把 2500 条岗位收敛成一份能照着做的短名单。

看板适合浏览和筛选，但每次打开都要自己挑。这份清单直接给结论：
先投哪几家、哪些快截止了。

三个板块：
  1. 国央企 · 14 天内截止 —— 真正会错过的东西
  2. 技术岗 · 按匹配分     —— 高分岗位
  3. 国央企 · 其余         —— 按截止日期排序

为什么"紧急"板块给国央企而不是技术岗：技术岗源里绝大多数写的是「招满即止」，
没有可排序的截止日期；而国聘网返回的国央企岗位都带真实 end_time。
实测技术岗 7 天内截止的只有个位数，且多为不对口的金融岗。

用法：
    python scripts/daily_list.py                 # 生成并打印
    python scripts/daily_list.py --tech 40       # 技术岗取 40 家
    python scripts/daily_list.py --include-intern # 把实习岗也带上
    python scripts/daily_list.py --open          # 顺带打开生成的清单
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, connect, days_until, fix_stdout, is_recent, now  # noqa: E402

OUT_PATH = ROOT / "今日投递清单.md"
# 15 分太低：「上海(15) + 本科(5)」这种基础分就能过线，会把金融分析员、
# 材料检测员这类明显不对口的岗位放进来。25 分对应「至少命中一个方向关键词」。
MIN_SCORE = 25.0


def collect(conn, table: str, limit: int, order: str, extra_where: str = "",
            params: tuple = ()) -> list[dict]:
    is_soe = table == "soe_jobs"
    where = "status = '待投递' AND match_score >= ?"
    if extra_where:
        where += f" AND {extra_where}"

    sql = f"SELECT * FROM {table} WHERE {where} ORDER BY {order} LIMIT ?"
    rows = conn.execute(sql, (MIN_SCORE, *params, limit)).fetchall()

    out = []
    for r in rows:
        dl = (r["end_time"] if is_soe else r["deadline"]) or ""
        out.append({
            "company": r["company"],
            "position": (r["job_name"] if is_soe else r["position"]) or "",
            "place": (r["district_cn"] if is_soe else r["locations"]) or "",
            "education": (r["education_cn"] if is_soe else "") or "",
            "score": round(r["match_score"] or 0, 1),
            "deadline": dl,
            "days": days_until(dl),
            "url": r["apply_url"] or "",
            "is_new": is_recent(r["first_seen"], 3),
        })
    return out


def parse_wage(w: str) -> float | None:
    """把各种薪资格式解析成月薪中位数（元）。

    "10000-15000 元/月" -> 12500    "5-15 千元/月" -> 10000
    "面议" / "130-150 元/天"（实习）-> None
    """
    import re as _re

    if not w or "面议" in w or "元/天" in w:
        return None
    unit = 1000 if "千元" in w else 1
    nums = [float(x) for x in _re.findall(r"\d+(?:\.\d+)?", w)]
    if not nums:
        return None
    return (min(nums) + max(nums)) / 2 * unit


# 国央企「待遇优先」视角用的专业匹配：比默认宽松得多。
# 用户的原话是「专业对口要求不用太高，有点专业关联就行」。
LOOSE_MAJOR = (
    "(majors LIKE '%计算机%' OR majors LIKE '%软件%' OR majors LIKE '%电子信息%' "
    "OR majors LIKE '%通信%' OR majors LIKE '%自动化%' OR majors LIKE '%数学%' "
    "OR majors LIKE '%统计%' OR majors LIKE '%信息%')"
)


def collect_soe_by_pay(conn, limit: int, intern_filter: str) -> list[dict]:
    """国央企按待遇排序。

    ⚠️ 放宽专业匹配后会捞进大量「专业范围写得宽、实际是传统工业岗」的职位
    （值班员、热控检修工、电解工……）。这些岗位专业要求里确实有「计算机类」，
    但工作内容和 IT 无关，必须靠人工识别。排序结果只能当线索，不能当结论。
    """
    sql = f"""SELECT * FROM soe_jobs
              WHERE status='待投递' AND education_cn LIKE '%本科%'
                AND {LOOSE_MAJOR} AND wage != '' AND wage NOT LIKE '%面议%'
                AND wage NOT LIKE '%元/天%'
                {'AND ' + intern_filter if intern_filter else ''}"""
    rows = []
    for r in conn.execute(sql):
        pay = parse_wage(r["wage"])
        if not pay:
            continue
        dl = r["end_time"] or ""
        rows.append({
            "company": r["company"],
            "position": r["job_name"] or "",
            "place": r["district_cn"] or "",
            "education": r["nature_cn"] or "",
            "score": round(r["match_score"] or 0, 1),
            "pay": pay,
            "deadline": dl,
            "days": days_until(dl),
            "url": r["apply_url"] or "",
            "is_new": is_recent(r["first_seen"], 3),
            "benefits": extract_benefits(r["jd"] or ""),
        })
    rows.sort(key=lambda x: -x["pay"])
    return rows[:limit]


def urgency(days) -> str:
    if days is None:
        return ""
    if days < 0:
        return "已截止"
    if days == 0:
        return "**今天截止**"
    if days <= 3:
        return f"**剩 {days} 天**"
    if days <= 7:
        return f"剩 {days} 天"
    return f"剩 {days} 天"


# 待遇关键词 -> 展示标签。顺序即优先级，命中多个时按此顺序取前几个。
#
# ⚠️ 覆盖率很低：583 条 JD 里只有 2 条写了「五险一金」。国央企的福利多是标准化
# 的、不写进 JD，所以「没提到」不等于「没有」。这个标签只能当**加分信号**，
# 不能当筛选条件。真正值钱的是「编制」和「落户」——那两条是明确信号。
BENEFIT_TAGS = [
    ("编制", "🏛编制"),
    ("落户", "🏠落户"), ("户口", "🏠落户"),
    ("七险二金", "💰七险二金"), ("六险二金", "💰六险二金"),
    ("五险一金", "💰五险一金"), ("公积金", "💰公积金"),
    ("安家费", "💵安家费"),
    ("住房补贴", "🏠住房补贴"),
    ("宿舍", "🛏宿舍"), ("住宿", "🛏住宿"),
    ("年终奖", "🎁年终奖"),
    ("餐补", "🍚餐补"), ("午餐", "🍚餐补"),
    ("补贴", "💵补贴"),
    ("体检", "🏥体检"),
]


def extract_benefits(jd: str) -> str:
    """从 JD 正文里提取待遇标签。命中即加，去重后按 BENEFIT_TAGS 顺序排列。"""
    if not jd:
        return ""
    seen: list[str] = []
    for kw, tag in BENEFIT_TAGS:
        if kw in jd and tag not in seen:
            seen.append(tag)
    return " ".join(seen[:3])


def render_pay_section(rows: list[dict]) -> list[str]:
    """国央企待遇优先视角。列的是薪资而非匹配分。"""
    out = [
        "\n## 国央企 · 待遇优先（放宽专业要求）\n",
        "按**月薪中位数**降序。专业只要求「有点关联」，所以会捞进大量"
        "**专业范围写得宽、实际是传统工业岗**的职位（值班员、检修工、电解工……）。\n",
        "> ⚠️ **必须点进 JD 确认工作内容是不是 IT/数字化**。"
        "专业要求写「计算机类」不代表岗位是技术岗。\n",
        "",
        "| # | 公司 | 岗位 | 地点 | 月薪 | 待遇 | 截止 | 投递 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        pos = r["position"][:30].replace("|", "/")
        place = r["place"][:14].replace("|", "/")
        new = " 🆕" if r["is_new"] else ""
        u = urgency(r["days"])
        dl = f"{r['deadline'][:10] or '—'}{' ' + u if u else ''}"
        link = f"[投递]({r['url']})" if r["url"] else "—"
        out.append(
            f"| {i} | {r['company'][:22]}{new} | {pos} | {place} | "
            f"**{r['pay']/1000:.1f}K** | {r.get('benefits') or '—'} | {dl} | {link} |"
        )
    out.append("")
    return out


def render_section(title: str, note: str, rows: list[dict], is_soe: bool) -> list[str]:
    if not rows:
        return [f"\n## {title}\n", "_（无）_\n"]

    out = [f"\n## {title}\n"]
    if note:
        out.append(f"{note}\n")
    out.append("")
    out.append("| # | 公司 | 岗位 | 地点 | 匹配 | 截止 | 投递 |")
    out.append("|---|---|---|---|---|---|---|")

    for i, r in enumerate(rows, 1):
        pos = r["position"][:46].replace("|", "/")
        place = r["place"][:20].replace("|", "/")
        new = " 🆕" if r["is_new"] else ""
        dl = f"{r['deadline'] or '招满即止'}"
        u = urgency(r["days"])
        if u:
            dl = f"{dl} {u}"
        link = f"[投递]({r['url']})" if r["url"] else "—"
        edu = f"（{r['education']}）" if is_soe and r["education"] else ""
        out.append(
            f"| {i} | {r['company'][:26]}{new} | {pos}{edu} | {place} | {r['score']:.0f} | {dl} | {link} |"
        )
    out.append("")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tech", type=int, default=30, help="技术岗取几家（默认 30）")
    ap.add_argument("--soe", type=int, default=20, help="国央企取几家（默认 20）")
    ap.add_argument("--include-intern", action="store_true", help="把实习岗也列入")
    ap.add_argument("--soe-pay", action="store_true",
                    help="追加国央企待遇优先视角（放宽专业要求，按薪资排序）")
    ap.add_argument("--open", action="store_true", help="生成后打开清单")
    args = ap.parse_args()

    fix_stdout()
    conn = connect()

    # 实习岗默认排除 —— 目标是 2027 届校招正职。
    # 只看岗位名会漏：广州公交「信息化助理」名字里没有「实习」，
    # 但薪资是 130-150 元/天、JD 写「全日制本科及以上学历在读」——妥妥的实习岗。
    # 所以按日计薪、要求在校生，都是实习的判据。
    INTERN = "(job_name LIKE '%实习%' OR wage LIKE '%元/天%' OR jd LIKE '%在读%')"
    intern_filter = "" if args.include_intern else f"NOT {INTERN}"
    intern_n = conn.execute(
        f"SELECT COUNT(*) FROM soe_jobs WHERE status='待投递' AND {INTERN}"
    ).fetchone()[0]

    # 1. 国央企：14 天内截止（真正会错过的）
    soe_urgent = collect(
        conn, "soe_jobs", 20,
        "end_time ASC",
        "end_time != '' AND julianday(end_time) - julianday('now') BETWEEN 0 AND 14"
        + (f" AND {intern_filter}" if intern_filter else ""),
    )

    # 2. 技术岗高分
    tech = collect(conn, "tech_jobs", args.tech, "match_score DESC")

    # 3. 国央企其余，按截止排序（无截止的排最后）
    #    要排掉板块一已经列过的，否则同一家公司出现两次
    shown = {(r["company"], r["position"]) for r in soe_urgent}
    soe = [r for r in collect(
        conn, "soe_jobs", args.soe + len(shown),
        "CASE WHEN end_time = '' OR end_time IS NULL THEN 1 ELSE 0 END, end_time ASC",
        intern_filter,
    ) if (r["company"], r["position"]) not in shown][:args.soe]

    total_pending = conn.execute(
        "SELECT (SELECT COUNT(*) FROM tech_jobs WHERE status='待投递') + "
        "(SELECT COUNT(*) FROM soe_jobs WHERE status='待投递')"
    ).fetchone()[0]

    # 必须在 close 之前把所有数据收集完
    pay_rows = collect_soe_by_pay(conn, 30, intern_filter) if args.soe_pay else []
    conn.close()

    lines = [
        "# 今日投递清单",
        "",
        f"生成时间：{now()}　|　待投递总数：{total_pending} 条",
        "",
        "> 从看板挑剩下会挑花眼，这份清单直接给结论。",
        "> 投完一家就在终端里记一笔：`python scripts/track.py 公司名 已投递`",
    ]

    lines += render_section(
        "一、国央企 · 14 天内截止",
        "**这些是真会错过的**，国聘网返回的岗位都带真实截止时间。今天优先解决。",
        soe_urgent, True)
    lines += render_section(
        f"二、技术岗（按匹配分 Top {args.tech}）",
        "匹配分综合了对口关键词与城市权重，上海的岗位有额外加权。"
        "技术岗多为「招满即止」，随时可能关，不要拖。",
        tech, False)
    lines += render_section(
        f"三、国央企 · 其余（按截止日期 Top {args.soe}）",
        "国央企走独立通道，必须你手动投递。无截止日期的排在最后。", soe, True)

    if pay_rows:
        lines += render_pay_section(pay_rows)

    if not args.include_intern and intern_n:
        lines += [
            "",
            f"> 另有 **{intern_n} 条实习岗**未列入（你找的是 2027 届正职）。"
            f"要看的话加 `--include-intern`。",
            "",
        ]

    lines += [
        "",
        "---",
        "",
        "## 投递流程",
        "",
        "1. 点「投递」打开官方页面",
        "2. 点书签栏的「填写网申表单」（没装的话见 `使用手册.md` 操作 1）",
        "3. 核对绿色描边的字段，补上手填项，上传简历 PDF",
        "4. **自己点提交**",
        "5. `python scripts/track.py 公司名 已投递`",
        "",
        "## 简历用哪个变体",
        "",
        "| 投什么岗位 | 变体 |",
        "|---|---|",
        "| 大模型应用、RAG、Agent | `llm-rag` |",
        "| 算法、微调、推理部署 | `ai-algo` |",
        "| 后端、服务端 | `python-backend` |",
        "| 国央企、事业单位 | `soe` |",
        "",
        "```bash",
        "python scripts/build_resume.py --variant llm-rag --pdf",
        "```",
        "",
    ]

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {OUT_PATH.relative_to(ROOT)}")
    print(f"  国央企 14 天内截止 {len(soe_urgent)} 家")
    print(f"  技术岗 Top {len(tech)}")
    print(f"  国央企其余 Top {len(soe)}")
    if intern_n and not args.include_intern:
        print(f"  已排除 {intern_n} 条实习岗")

    if soe_urgent:
        print("\n最紧急的几家：")
        for r in soe_urgent[:5]:
            print(f"  {urgency(r['days']):>14}  {r['company'][:24]} — {r['position'][:30]}")

    if args.open:
        subprocess.Popen(["cmd", "/c", "start", "", str(OUT_PATH)], shell=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
