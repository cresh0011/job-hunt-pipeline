"""生成面试题库，并支持把面试实战中遇到的问题回流进题库。

    python scripts/build_questions.py                          # 全部题目
    python scripts/build_questions.py --jd jd.txt              # 按 JD 关键词排序
    python scripts/build_questions.py --company 米哈游 --role 大模型应用开发
    python scripts/build_questions.py --collect-retro          # 把复盘里的题回流

复盘回流是这套系统里**复利最高**的一环：每面完一场，把真实被问到的题记进
interview/retro/，下次生成的题库就多一层实战标注。第三次面试的准备质量会
明显高于第一次。
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, fix_stdout  # noqa: E402

QUESTIONS_PATH = ROOT / "interview" / "questions.yaml"
FACTS_PATH = ROOT / "data" / "resume" / "facts.yaml"
BANK_DIR = ROOT / "interview" / "question_bank"
RETRO_DIR = ROOT / "interview" / "retro"

RETRO_TEMPLATE = """# 面试复盘 — {company} {role}

- **日期**：{today}
- **岗位**：
- **轮次**：一面 / 二面 / 三面 / HR 面
- **面试官**：（技术 / HR / 主管）
- **结果**：待定

## 被问到的问题

<!--
按下面格式逐条记录，`build_questions.py --collect-retro` 会自动收集。
「我的回答」和「改进」写得越具体，回流价值越高。
-->

### Q:
- **我的回答**：
- **结果**：答得好 / 答得一般 / 没答上来
- **改进**：

## 我没答上来的点

## 面试官强调的能力

## 反问环节我问了什么，对方怎么答的

## 其他观察
（团队氛围、技术栈、流程、面试官关注点等）
"""


def load_yaml(path: Path):
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def collect_retro() -> dict[str, list[str]]:
    """扫描复盘文件，收集实战被问到的题目，按关键词归类。

    返回 {分类 id: [题目原文]}。分类靠题干关键词粗匹配，匹配不上归入 general。
    """
    found: dict[str, list[str]] = {}
    for f in sorted(RETRO_DIR.glob("*.md")):
        if f.name.startswith("_"):
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^###\s*Q[:：]\s*(.+)$", text, re.M):
            q = m.group(1).strip()
            if q:
                found.setdefault(f.stem, []).append(q)
    return found


def render(tracks: list[dict], facts: dict, header: str, retro: dict) -> str:
    out: list[str] = [header]

    if retro:
        out.append("\n---\n\n## 实战被问到过的题\n")
        out.append("（来自 interview/retro/ 的复盘记录，按面试场次分组）\n")
        for src, qs in retro.items():
            out.append(f"\n**{src}**\n")
            for q in qs:
                out.append(f"- {q}")
        out.append("")

    for i, t in enumerate(tracks, 1):
        star = " ⭐ 重点" if t.get("priority") == 1 else ""
        out.append(f"\n---\n\n## {i}. {t['title']}{star}\n")
        if t.get("intro"):
            out.append("> " + t["intro"].strip().replace("\n", "\n> ") + "\n")

        for j, q in enumerate(t["questions"], 1):
            out.append(f"\n### Q{j}. {q['q']}\n")
            if q.get("intent"):
                out.append(f"**面试官想验证**：{q['intent']}\n")
            if q.get("answer_hint"):
                out.append("**回答提示**：")
                out.append("> " + q["answer_hint"].strip().replace("\n", "\n> ") + "\n")
            if q.get("follow_ups"):
                out.append("**追问链**：")
                for fu in q["follow_ups"]:
                    out.append(f"- {fu}")
                out.append("")

    # 附上项目关键数据，准备时对着数字看
    out.append("\n---\n\n## 附：项目关键数据（备查）\n")
    for p in facts["projects"]:
        out.append(f"\n### {p['name']}\n")
        for b in p.get("bullets") or []:
            out.append(f"- {b}")
        if p.get("insight"):
            out.append(f"- **洞察**：{p['insight']}")
        if p.get("defects_found"):
            out.append("- **发现的度量缺陷**：")
            for d in p["defects_found"]:
                out.append(f"  - {d}")
        if p.get("data_disclaimer"):
            out.append(f"- ⚠️ **{p['data_disclaimer']}**")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jd", help="JD 文本文件路径，按关键词给分类排序")
    ap.add_argument("--company", default="", help="公司名，用于输出文件名")
    ap.add_argument("--role", default="", help="岗位名，用于输出文件名")
    ap.add_argument("--collect-retro", action="store_true", help="收集复盘题目并回流")
    ap.add_argument("--retro", metavar="COMPANY", help="为该公司生成一份复盘模板")
    args = ap.parse_args()

    fix_stdout()
    data = load_yaml(QUESTIONS_PATH)
    facts = load_yaml(FACTS_PATH)
    tracks = data["tracks"]

    # --- 生成复盘模板 ---
    if args.retro:
        RETRO_DIR.mkdir(parents=True, exist_ok=True)
        path = RETRO_DIR / f"{args.retro}_{date.today().isoformat()}.md"
        if path.exists():
            print(f"已存在：{path.relative_to(ROOT)}")
            return 1
        path.write_text(
            RETRO_TEMPLATE.format(company=args.retro, role=args.role or "（待填）",
                                  today=date.today().isoformat()),
            encoding="utf-8")
        print(f"已生成复盘模板 {path.relative_to(ROOT)}")
        print("  面试完填好它，再跑 --collect-retro 把题目回流进题库。")
        return 0

    # --- 按 JD 排序分类 ---
    jd_text = ""
    if args.jd:
        jd_text = Path(args.jd).read_text(encoding="utf-8")
    low = jd_text.lower()

    def score(t: dict) -> tuple:
        kws = t.get("trigger_keywords") or []
        hits = sum(1 for k in kws if str(k).lower() in low)
        # 先按命中数降序，再按 priority 升序（priority 1 最优先）
        return (-hits, t.get("priority", 9))

    tracks = sorted(tracks, key=score)

    retro = collect_retro() if args.collect_retro else {}

    title = "面试题库"
    if args.company:
        title += f" — {args.company}"
    if args.role:
        title += f" {args.role}"

    header = f"# {title}\n\n- 生成日期：{date.today().isoformat()}"
    if args.jd:
        header += f"\n- JD 来源：`{args.jd}`（分类已按关键词命中度排序）"
    header += ("\n\n> 用法：先过一遍 ⭐ 重点分类，把追问链在心里走一遍。"
               "\n> 面试完用 `--retro <公司名>` 生成复盘模板，再 `--collect-retro` 回流。")

    md = render(tracks, facts, header, retro)

    BANK_DIR.mkdir(parents=True, exist_ok=True)
    if args.company:
        stem = re.sub(r'[\\/:*?"<>|]', "_", f"{args.company}_{args.role or '通用'}")
    else:
        stem = "通用题库"
    path = BANK_DIR / f"{stem}.md"
    path.write_text(md, encoding="utf-8")

    n_q = sum(len(t["questions"]) for t in tracks)
    print(f"已生成 {path.relative_to(ROOT)}")
    print(f"  {len(tracks)} 个分类，{n_q} 道题")
    print(f"  分类顺序：{' → '.join(t['title'] for t in tracks)}")
    if retro:
        print(f"  已回流 {sum(len(v) for v in retro.values())} 道实战题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
