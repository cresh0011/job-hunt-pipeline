"""投递进度追踪：按公司名模糊查找岗位、更新状态、查看统计。

岗位表上的 status 是当前状态（便于查询），applications 表是只追加的事件日志
（支撑进度时间线）。本脚本两者都写。

用法：
    python scripts/track.py 米哈游 已投递            # 该公司所有条目一并更新
    python scripts/track.py 米哈游 一面 --note "问了 RAG 评测"
    python scripts/track.py 米哈游 二面 --pick 2     # 只更新第 2 条
    python scripts/track.py --list                   # 看所有已推进的
    python scripts/track.py --list --status-filter 面试
    python scripts/track.py --stats                  # 漏斗统计
    python scripts/track.py --undo 米哈游            # 退回待投递
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import connect, fix_stdout, log_event, now  # noqa: E402

# 状态归一化：用户可能写"一面""二面"，统计时都归到「面试」
STATUS_ALIASES = {
    "已投": "已投递", "投递": "已投递", "投了": "已投递",
    "笔试中": "笔试", "测评": "笔试",
    "一面": "面试", "二面": "面试", "三面": "面试", "终面": "面试",
    "hr面": "面试", "HR面": "面试", "面试中": "面试",
    "offer": "Offer", "OFFER": "Offer", "录取": "Offer",
    "挂了": "感谢信", "拒信": "感谢信", "未通过": "感谢信",
    "放弃": "放弃", "不去了": "放弃",
}

TRACKS = [("tech_jobs", "tech", "技术岗"), ("soe_jobs", "soe", "国央企")]
FUNNEL = ["待投递", "已投递", "笔试", "面试", "Offer"]


def find(conn, keyword: str) -> list[dict]:
    """按公司名模糊匹配。

    必须带上 job_id —— 同一公司常有多条记录，若下游按 company 反查再用
    fetchone()，会把同一条反复更新，其余条目永远改不到。
    """
    hits: list[dict] = []
    for table, track, label in TRACKS:
        name_col = "job_name" if table == "soe_jobs" else "position"
        rows = conn.execute(
            f"SELECT job_id, company, {name_col} AS jn, status, match_score FROM {table} "
            f"WHERE company LIKE ? ORDER BY match_score DESC LIMIT 10",
            (f"%{keyword}%",),
        ).fetchall()
        for r in rows:
            hits.append({
                "job_id": r["job_id"], "table": table, "track": track, "label": label,
                "company": r["company"], "jn": r["jn"] or "",
                "status": r["status"] or "待投递", "score": r["match_score"] or 0,
            })
    return hits


def do_update(args) -> int:
    """更新投递状态。

    同一家公司常有多个条目（米哈游 2 条、小红书 6 条 —— 提前批/正式批/不同岗位线）。
    用户的心智模型是「我投了米哈游」而不是「我投了米哈游的第 3 条」，
    所以默认把该公司下**所有**条目一并更新，并告知影响了几条。
    想只改一条时，用 --pick N 从列表里选。
    """
    conn = connect()
    hits = find(conn, args.keyword)
    if not hits:
        print(f"没找到公司名含「{args.keyword}」的岗位。")
        print("  提示：用公司名的连续片段，比如「米哈游」「上海左禧」。")
        return 1

    status = STATUS_ALIASES.get(args.status, args.status)

    # --pick N：只更新选中的那一条
    if args.pick:
        if not 1 <= args.pick <= len(hits):
            print(f"--pick 超出范围（1-{len(hits)}）")
            for i, h in enumerate(hits, 1):
                print(f"  {i}. [{h['label']}] {h['company']} — {h['jn'][:40]}")
            return 1
        hits = [hits[args.pick - 1]]

    # 公司名完全相等时只更那几条，避免「小红书」把「小红书Ace」也带上
    elif len(hits) > 1:
        exact = [h for h in hits if h["company"] == args.keyword]
        if exact:
            hits = exact

    updated = 0
    for h in hits:
        conn.execute(f"UPDATE {h['table']} SET status = ? WHERE job_id = ?",
                     (status, h["job_id"]))
        log_event(conn, h["job_id"], h["track"], status, args.note or "")
        updated += 1
        print(f"  [{h['label']}] {h['company'][:30]:<32} {h['status']}  ->  {status}")

    conn.commit()
    conn.close()

    if not updated:
        print("没有条目被更新。")
        return 1

    print(f"\n已更新 {updated} 条（{now()}）")
    if args.note:
        print(f"备注：{args.note}")
    if len(hits) > 1:
        print(f"提示：这家公司有 {len(hits)} 个条目，已一并更新。"
              f"只想改一条用 --pick N。")
    print("跑 `python scripts/dashboard.py` 刷新看板。")
    return 0


def do_undo(args) -> int:
    conn = connect()
    hits = find(conn, args.keyword)
    if not hits:
        print(f"没找到「{args.keyword}」")
        return 1
    exact = [h for h in hits if h["company"] == args.keyword] or hits
    for h in exact:
        conn.execute(f"UPDATE {h['table']} SET status = '待投递' WHERE job_id = ?",
                     (h["job_id"],))
        print(f"  [{h['label']}] {h['company']} 退回「待投递」")
    conn.commit()
    conn.close()
    return 0


def do_list(args) -> int:
    conn = connect()
    rows: list[tuple] = []
    for table, track, label in TRACKS:
        name_col = "job_name" if table == "soe_jobs" else "position"
        sql = (f"SELECT company, {name_col} AS jn, status, match_score FROM {table} "
               f"WHERE status != '待投递'")
        params: list = []
        if args.status:
            sql += " AND status = ?"
            params.append(args.status)
        sql += " ORDER BY match_score DESC"
        for r in conn.execute(sql, params):
            rows.append((label, r["company"], r["jn"] or "", r["status"], r["match_score"]))

    if not rows:
        print("还没有推进中的投递。用 `track.py <公司名> 已投递` 开始记录。")
        return 0

    print(f"{'轨道':<8}{'公司':<30}{'状态':<10}{'分':>6}  岗位")
    print("-" * 100)
    for label, company, jn, status, score in rows:
        print(f"{label:<8}{company[:28]:<30}{status:<10}{score:>6.0f}  {jn[:36]}")
    print(f"\n共 {len(rows)} 条")
    conn.close()
    return 0


def do_stats(args) -> int:
    conn = connect()
    print("投递漏斗")
    print("-" * 40)
    total = 0
    counts = {s: 0 for s in FUNNEL}
    other = 0
    for table, _, label in TRACKS:
        for r in conn.execute(f"SELECT status, COUNT(*) c FROM {table} GROUP BY status"):
            s = r["status"] or "待投递"
            if s in counts:
                counts[s] += r["c"]
            else:
                other += r["c"]
            total += r["c"]

    for s in FUNNEL:
        n = counts[s]
        bar = "█" * min(40, n) if s != "待投递" else ""
        print(f"  {s:<8}{n:>6}  {bar}")
    if other:
        print(f"  {'其他':<8}{other:>6}")

    applied = total - counts["待投递"]
    print(f"\n  已推进 {applied} / {total}" +
          (f"（{applied/total*100:.1f}%）" if total else ""))

    rows = conn.execute(
        "SELECT job_id, track, status, changed_at, notes FROM applications "
        "ORDER BY id DESC LIMIT 12"
    ).fetchall()
    if rows:
        print("\n最近动态")
        print("-" * 40)
        for r in rows:
            note = f"  「{r['notes']}」" if r["notes"] else ""
            print(f"  {r['changed_at'][:16]}  {r['status']}{note}")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="投递进度追踪")
    ap.add_argument("keyword", nargs="?", help="公司名关键词")
    ap.add_argument("status", nargs="?", help="新状态，如：已投递 / 笔试 / 一面 / offer")
    ap.add_argument("--note", default="", help="备注")
    ap.add_argument("--pick", type=int, help="该公司命中多条时，只更新第 N 条")
    ap.add_argument("--list", action="store_true", help="列出推进中的投递")
    ap.add_argument("--status-filter", dest="status_filter", help="配合 --list 过滤状态")
    ap.add_argument("--stats", action="store_true", help="漏斗统计与最近动态")
    ap.add_argument("--undo", metavar="COMPANY", help="退回待投递")
    args = ap.parse_args()

    fix_stdout()

    if args.stats:
        return do_stats(args)
    if args.undo:
        args.keyword = args.undo
        return do_undo(args)
    if args.list:
        args.status = args.status_filter
        return do_list(args)
    if args.keyword and args.status:
        return do_update(args)

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
