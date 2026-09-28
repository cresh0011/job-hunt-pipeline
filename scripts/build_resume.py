"""简历生成：facts.yaml + 方向变体 → HTML → PDF。

**本脚本只做「选择、排序、改写措辞」，不引入任何新事实。**
所有内容都来自 data/resume/facts.yaml。它是防编造机制的执行者。

PDF 通过 Edge 的 --print-to-pdf 生成 —— Windows 自带 Chromium 内核浏览器，
无需安装 Playwright（省掉约 150MB 依赖）。

用法：
    python scripts/build_resume.py --variant llm-rag
    python scripts/build_resume.py --all --pdf
    python scripts/build_resume.py --variant soe --jd path/to/jd.txt
    python scripts/build_resume.py --list
"""

from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, fix_stdout, load_config  # noqa: E402

FACTS_PATH = ROOT / "data" / "resume" / "facts.yaml"
VARIANTS_PATH = ROOT / "resume" / "variants.yaml"
TEMPLATE_PATH = ROOT / "resume" / "templates" / "resume.html.j2"
OUT_DIR = ROOT / "resume" / "output"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

# 技能分类的中文标签。顺序由变体的 skill_order 决定。
# 标签必须准确：Scikit-Learn 不是深度学习框架；BERT 和 LLaMA/Qwen 不是一类模型。
SKILL_LABELS = {
    "languages":   "编程语言",
    "ml":          "机器学习 / 深度学习",
    "models":      "预训练模型",
    "application": "大模型应用",
    "backend":     "后端与工程",
}

# 主修课程 18 门全列会稀释重点，按目标岗位取相关的 8 门。
# 不放 Python —— 编程语言放在「主修课程」里不合体例。
COURSE_SUBSET = ["数据结构", "计算机组成原理", "操作系统", "计算机网络",
                 "数据库", "机器学习", "大数据与数据挖掘", "人工智能"]


# ---------------------------------------------------------------- 载入


def load_yaml(path: Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- 构造


def build_skills(facts: dict, variant: dict) -> list[dict]:
    """按变体指定的顺序把技能分类拼成可读文本。"""
    sk = facts["skills"]
    out: list[dict] = []
    for key in variant.get("skill_order", list(SKILL_LABELS)):
        items = sk.get(key)
        if not items:
            continue
        if key == "models":
            # 「理解 Transformer/GPT 架构」是能力描述，不是模型名，单独接在后面
            names = [i for i in items if "架构" not in i]
            rest = [i for i in items if "架构" in i]
            text = "、".join(names)
            if rest:
                text += "；理解 " + "、".join(rest)
        else:
            text = "、".join(items)
        out.append({"label": SKILL_LABELS.get(key, key), "text": text})
    return out


def build_honors(facts: dict) -> list[dict]:
    """把 honors 列表按名称归并成若干行，避免同一奖项重复占行。"""
    groups: dict[str, list] = {}
    order: list[str] = []
    for h in facts["honors"]:
        name = h["name"]
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(h)

    out: list[dict] = []
    for name in order:
        entries = groups[name]
        if name == "校级综合奖学金二等奖" or len(entries) == 1 and entries[0].get("count"):
            continue  # 由下面的「校级综合奖学金」合并行统一处理
        if entries[0].get("count"):
            continue
        if len(entries) > 1:
            out.append({"date": "", "text": f"{name}（{len(entries)} 次）"})
        else:
            out.append({"date": entries[0].get("date", ""), "text": name})

    # 校级奖学金合并成一行
    second = next((h for h in facts["honors"] if h["name"].endswith("二等奖")), None)
    third = next((h for h in facts["honors"] if h["name"].endswith("三等奖")), None)
    if second or third:
        parts = []
        if second:
            parts.append(f"二等奖 {second['count']} 次")
        if third:
            parts.append(f"三等奖 {third['count']} 次")
        out.insert(1, {"date": "2023.09-2026.07",
                       "text": "校级综合奖学金　" + "、".join(parts)})
    return out


def build_experiences(facts: dict, variant: dict) -> list[dict]:
    detail = variant.get("experience_detail", {})
    out: list[dict] = []
    for x in facts["experiences"]:
        bullets = list(x["bullets"])
        if detail.get(x["org"]) == "compact":
            bullets = bullets[:1]
        out.append({**x, "bullets": bullets})
    return out


def build_campus(facts: dict, variant: dict) -> list[dict]:
    """校园经历对技术岗价值最低，用 campus_n 控制保留几条要点以压住页数。

    国央企变体保留全部（团队与志愿经历在那类单位是加分项）。
    """
    n = variant.get("campus_n", 1)
    out: list[dict] = []
    for c in facts["campus"]:
        bullets = list(c["bullets"])
        if n is not None:
            bullets = bullets[:n]
        out.append({**c, "bullets": bullets})
    return out


def build_projects(facts: dict, variant: dict) -> list[dict]:
    order = variant.get("project_order") or [p["name"] for p in facts["projects"]]
    by_name = {p["name"]: p for p in facts["projects"]}
    detail = variant.get("project_detail", {})
    n_compact = variant.get("compact_n", 2)

    out: list[dict] = []
    for name in order:
        p = by_name.get(name)
        if not p:
            continue
        bullets = list(p.get("bullets") or [])
        if detail.get(name) == "compact":
            bullets = bullets[:n_compact]
        out.append({
            "name": p["name"],
            "role": p.get("role", ""),
            "org": p.get("org", ""),
            "year": p.get("year", ""),
            "stack": p.get("stack", []),
            "summary": "" if bullets else p.get("summary", ""),
            "bullets": bullets,
            "repo": p.get("repo", ""),
        })
    return out


def build_resume(facts: dict, variant: dict) -> dict:
    edu = []
    for e in facts["education"]:
        courses = [c for c in COURSE_SUBSET if c in e.get("courses", [])]
        edu.append({**e, "courses": courses})

    return {
        "basic": facts["basic"],
        "headline": variant["headline"],
        "grad": facts["education"][0]["end"].replace(".", "-"),
        "section_order": variant["sections"],
        "education": edu,
        "skills": build_skills(facts, variant),
        "experiences": build_experiences(facts, variant),
        "projects": build_projects(facts, variant),
        "honors": build_honors(facts),
        "campus": build_campus(facts, variant),
        "evaluation": facts.get("self_evaluation_condensed", []),
    }


# ---------------------------------------------------------------- JD 分析


# 同义词组：JD 里出现的词，只要组内任一同义词在事实库中，就算已覆盖。
# 不加这层会把「智能体」判成 Agent 的缺口、把「检索增强」判成 RAG 的缺口，
# 满屏误报会让这个功能失去意义。
_SYNONYM_GROUPS = [
    {"智能体", "Agent"},
    {"检索增强", "RAG"},
    {"生成式", "AIGC"},
    {"自然语言", "NLP"},
    {"大模型", "LLM"},
    {"深度学习", "机器学习"},
]

# 岗位名称类词汇，是职位的名字不是技能要求，不该出现在缺口里
_JD_NOISE = {"开发工程师", "研发工程师", "算法工程师", "后端", "服务端", "测试开发"}


def _synonym_map() -> dict[str, set[str]]:
    m: dict[str, set[str]] = {}
    for group in _SYNONYM_GROUPS:
        for term in group:
            m[term] = group
    return m


def analyze_jd(jd_text: str, facts: dict, cfg: dict) -> dict:
    """对照 JD 检查事实库覆盖情况。

    这是「不得编造」约束的落地机制：与其替用户编一个能力，不如如实报告缺口。
    报出的缺口必须是真缺口 —— 宁可漏报也不误报。
    """
    low = jd_text.lower()
    have_blob = " ".join([
        " ".join(facts["skills"].get(k, [])) for k in facts["skills"]
    ]) + " " + " ".join(
        b for p in facts["projects"] for b in (p.get("bullets") or [])
    ) + " " + " ".join(
        b for x in facts["experiences"] for b in x["bullets"]
    )
    have_low = have_blob.lower()
    syn = _synonym_map()

    def satisfied(kw: str) -> bool:
        return any(t.lower() in have_low for t in syn.get(kw, {kw}))

    covered: list[str] = []
    missing: list[str] = []
    for kw, weight in cfg["keyword_weights"].items():
        kw = str(kw)
        if kw in _JD_NOISE or kw.lower() not in low:
            continue
        (covered if satisfied(kw) else missing).append(kw)

    # 按权重排序，重要的排前面
    covered.sort(key=lambda k: -cfg["keyword_weights"][k])
    missing.sort(key=lambda k: -cfg["keyword_weights"][k])
    return {"covered": covered, "missing": missing}


def pick_variant(jd_text: str, variants: dict, cfg: dict) -> str:
    """按 JD 关键词命中度自动选最合适的变体。"""
    low = jd_text.lower()
    best, best_score = "llm-rag", -1.0
    for key, v in variants["variants"].items():
        blob = (v["headline"] + " " + " ".join(v["skill_order"])).lower()
        score = sum(w for kw, w in cfg["keyword_weights"].items() if str(kw).lower() in low)
        # 后端岗的关键词在 headline 里体现得更明确，做个轻量偏置
        if "后端" in jd_text and key == "python-backend":
            score += 8
        if ("国企" in jd_text or "央企" in jd_text or "事业单位" in jd_text) and key == "soe":
            score += 25
        if score > best_score:
            best, best_score = key, score
    return best


# ---------------------------------------------------------------- 渲染


def render_html(facts: dict, variant: dict, out_stem: str) -> Path:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_PATH.parent)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tpl = env.get_template(TEMPLATE_PATH.name)
    r = build_resume(facts, variant)
    html_text = tpl.render(r=r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{out_stem}.html"
    path.write_text(html_text, encoding="utf-8")
    return path


def to_pdf(html_path: Path, pdf_path: Path, wait_s: int = 30) -> bool:
    """用 Edge/Chrome 的 --print-to-pdf 生成 PDF。

    两个必须的细节：
      1. --user-data-dir 指向独立临时目录。否则 Edge 检测到已有实例时可能
         直接把任务转交过去并立即返回，什么都不打印。
      2. 进程退出 ≠ 文件写完。实测 Edge 会先返回、后落盘，所以必须轮询等待，
         不能 subprocess.run 一回来就 exists()。
    """
    import tempfile
    import time

    exe = next((p for p in EDGE_CANDIDATES if Path(p).exists()), None)
    if not exe:
        print("  [!] 找不到 Edge/Chrome，跳过 PDF 导出（HTML 已生成）")
        return False

    if pdf_path.exists():
        pdf_path.unlink()

    profile = Path(tempfile.gettempdir()) / "jobhunt-pdf-profile"
    url = "file:///" + str(html_path).replace("\\", "/")

    try:
        subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", url],
            capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("  [!] PDF 导出超时")
        return False

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return True
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", help="变体 key（见 --list）")
    ap.add_argument("--all", action="store_true", help="生成全部变体")
    ap.add_argument("--pdf", action="store_true", help="同时导出 PDF")
    ap.add_argument("--jd", help="JD 文本文件路径：自动选变体并报告关键词缺口")
    ap.add_argument("--list", action="store_true", help="列出所有变体")
    args = ap.parse_args()

    fix_stdout()
    facts = load_yaml(FACTS_PATH)
    variants = load_yaml(VARIANTS_PATH)["variants"]
    cfg = load_config()

    if args.list:
        print("可用变体：")
        for k, v in variants.items():
            print(f"  {k:16s} {v['label']}")
        return 0

    if not facts.get("meta", {}).get("verified_by_user"):
        print("[!] 警告：facts.yaml 尚未标记为用户已核对，生成前请确认事实准确。\n")

    jd_text = ""
    if args.jd:
        jd_text = Path(args.jd).read_text(encoding="utf-8")
        if not args.variant and not args.all:
            auto = pick_variant(jd_text, {"variants": variants}, cfg)
            print(f"按 JD 自动选择变体：{auto}（{variants[auto]['label']}）\n")
            args.variant = auto

        report = analyze_jd(jd_text, facts, cfg)
        print("=" * 66)
        print("  JD 关键词覆盖分析")
        print("=" * 66)
        print(f"  已覆盖（{len(report['covered'])}）："
              + ("、".join(report["covered"]) or "无"))
        print(f"  缺口（{len(report['missing'])}）："
              + ("、".join(report["missing"]) or "无"))
        if report["missing"]:
            print()
            print("  !! 以上关键词是 JD 明确要求、但事实库中没有的。")
            print("  !! 不要为它们编造经历。要么如实承认，要么去补真实项目经验。")
        print()

    targets = list(variants) if args.all else ([args.variant] if args.variant else [])
    if not targets:
        print("请指定 --variant <key>、--all，或用 --list 查看可用变体。")
        return 1

    for key in targets:
        v = variants.get(key)
        if not v:
            print(f"[X] 未知变体：{key}")
            continue
        stem = key
        html_path = render_html(facts, v, stem)
        print(f"[OK] {v['label']:22s} -> {html_path.relative_to(ROOT)}")
        if args.pdf:
            pdf_path = OUT_DIR / f"{stem}.pdf"
            if to_pdf(html_path, pdf_path):
                print(f"     PDF -> {pdf_path.relative_to(ROOT)}")
            else:
                print("     PDF 导出失败")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
