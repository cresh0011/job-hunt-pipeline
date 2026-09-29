"""生成 dashboard.html —— 单文件、离线可用、浅深色自适应的求职看板。

设计遵循数据可视化规范：
  - 形式是「统计卡 + 表格」，不是图表（数据的工作是识别与排序，不是展示趋势）
  - 截止紧急度用状态色（good/warning/serious/critical），且必带图标+文字，
    绝不靠颜色单独表意
  - 投递阶段是「有序量级」，用单一蓝色顺序色阶（已通过 validate_palette.js 校验）
  - 文字一律用文本色，颜色只出现在标记上；大数字用比例字形，表格列用等宽字形

用法：
    python scripts/dashboard.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ROOT, connect, days_until, fix_stdout, is_recent, load_config, now,
)

OUT_PATH = ROOT / "dashboard.html"

# 投递阶段（有序）。索引即色阶档位，顺序不可乱。
STAGES = ["待投递", "已投递", "笔试", "面试", "Offer"]

# 「临近截止」只统计分数过这条线的岗位。
# 不设这条线的话，-39 分的专利代理师和 3 分的证券客户经理都会被算成"紧急" ——
# KPI 会虚高到失去指引作用（实测 55 条里只有个位数是对的）。
# 与 daily_list.py 的 MIN_SCORE 保持一致。
RELEVANT_SCORE = 25.0
STAGE_ALIASES = {
    "待投递": "待投递",
    "已投递": "已投递",
    "笔试": "笔试",
    "一面": "面试", "二面": "面试", "三面": "面试", "HR面": "面试", "面试中": "面试",
    "offer": "Offer", "Offer": "Offer",
}


def deadline_level(days: int | None, raw: str) -> str:
    """把剩余天数映射到状态等级。返回 critical / serious / warning / normal / none。"""
    if days is None:
        return "none" if raw else "none"
    if days < 0:
        return "critical"
    if days <= 3:
        return "critical"
    if days <= 7:
        return "serious"
    if days <= 14:
        return "warning"
    return "normal"


def is_internship(r, is_soe: bool) -> bool:
    """判断是不是实习岗。

    只看岗位名会漏：广州公交「信息化助理」名字里没有「实习」二字，
    但薪资是 130-150 元/天、JD 写「全日制本科及以上学历在读」——妥妥的实习岗。
    所以按日计薪、要求在校生，都是判据。与 daily_list.py 保持一致。
    """
    name = (r["job_name"] if is_soe else r["position"]) or ""
    if "实习" in name:
        return True
    if not is_soe:
        return False
    wage = r["wage"] or ""
    jd = r["jd"] or ""
    return "元/天" in wage or "在读" in jd


def build_rows(conn, table: str, cfg: dict) -> list[dict]:
    """从库里取岗位，转成前端需要的扁平结构。"""
    order = "match_score DESC"
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    out: list[dict] = []

    for r in rows:
        is_soe = table == "soe_jobs"
        dl_raw = (r["end_time"] if is_soe else r["deadline"]) or ""
        days = days_until(dl_raw)

        out.append({
            "id": r["job_id"],
            "company": r["company"],
            "position": (r["job_name"] if is_soe else r["position"]) or "",
            "place": (r["district_cn"] if is_soe else r["locations"]) or "",
            "nature": (r["nature_cn"] if is_soe else r["batch"]) or "",
            "education": (r["education_cn"] if is_soe else "") or "",
            "score": round(r["match_score"] or 0, 1),
            "deadline": dl_raw,
            "days": days,
            "level": deadline_level(days, dl_raw),
            "url": r["apply_url"] or "",
            "status": r["status"] or "待投递",
            "is_new": is_recent(r["first_seen"], cfg["dashboard"]["new_days"]),
            "source": r["source"] or "",
            "is_intern": is_internship(r, is_soe),
        })
    return out


def build_funnel(tech: list[dict], soe: list[dict]) -> list[dict]:
    """按阶段统计投递进度。未知状态归入「其他」，不计入色阶。"""
    counts = {s: 0 for s in STAGES}
    for row in tech + soe:
        stage = STAGE_ALIASES.get(row["status"])
        if stage:
            counts[stage] += 1
    return [{"stage": s, "count": counts[s]} for s in STAGES]


def main() -> int:
    fix_stdout()
    cfg = load_config()
    conn = connect()

    tech = build_rows(conn, "tech_jobs", cfg)
    soe = build_rows(conn, "soe_jobs", cfg)
    funnel = build_funnel(tech, soe)

    all_rows = tech + soe
    new_count = sum(1 for r in all_rows if r["is_new"])
    pending = sum(1 for r in all_rows if r["status"] == "待投递")
    interviewing = sum(1 for r in all_rows if STAGE_ALIASES.get(r["status"]) == "面试")
    # 只统计「对口 + 非实习」的临近截止岗位。
    # 两个条件缺一不可：不筛分数会把 -39 分的专利代理师算成紧急；
    # 不筛实习会把实习岗算成该投的岗位（用户找的是 2027 届正职）。
    def _urgent(r) -> bool:
        return (r["level"] in ("critical", "serious")
                and (r["days"] or 0) >= 0)

    urgent = sum(1 for r in all_rows
                 if _urgent(r) and r["score"] >= RELEVANT_SCORE and not r["is_intern"])
    # 顺带记下放宽后的数量，副文字里说明，避免读者以为漏了
    urgent_all = sum(1 for r in all_rows if _urgent(r))
    expired = sum(1 for r in all_rows if (r["days"] is not None and r["days"] < 0))

    data = {
        "generated": now(),
        "stats": {
            "total": len(all_rows),
            "tech": len(tech),
            "soe": len(soe),
            "new": new_count,
            "pending": pending,
            "interviewing": interviewing,
            "urgent": urgent,
            "urgentAll": urgent_all,
            "expired": expired,
        },
        "funnel": funnel,
        "tech": tech,
        "soe": soe,
        "stages": STAGES,
        "warnDays": cfg["dashboard"]["deadline_warn_days"],
        "topN": cfg["dashboard"]["top_n"],
    }

    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    OUT_PATH.write_text(html, encoding="utf-8")

    print(f"已生成 {OUT_PATH}")
    print(f"  技术岗 {len(tech)} 条 | 国央企 {len(soe)} 条")
    print(f"  今日新增 {new_count} | 待投递 {pending} | 面试中 {interviewing}")
    print(f"  临近截止 {urgent} 条 | 已截止 {expired} 条")
    conn.close()
    return 0


# ------------------------------------------------------------------ 模板
# 用 __DATA__ 占位符而非 f-string —— 模板里大量 CSS/JS 花括号会与之冲突。

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2027 届秋招看板</title>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --page:    #f9f9f7;
  --ink-1:   #0b0b0b;
  --ink-2:   #52514e;
  --ink-3:   #898781;
  --grid:    #e1e0d9;
  --border:  rgba(11,11,11,0.10);
  --wash:    rgba(11,11,11,0.035);
  --seq-fill:  #2a78d6;
  --seq-track: #cde2fb;
  /* 状态色：固定，浅深色一致，不随主题变化 */
  --st-good:     #0ca30c;
  --st-warning:  #fab219;
  --st-serious:  #ec835a;
  --st-critical: #d03b3b;
  /* 投递阶段：蓝色顺序色阶（5 档，浅色） */
  --stage-0: #86b6ef; --stage-1: #5598e7; --stage-2: #2a78d6;
  --stage-3: #1c5cab; --stage-4: #104281;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19;
    --page:    #0d0d0d;
    --ink-1:   #ffffff;
    --ink-2:   #c3c2b7;
    --ink-3:   #898781;
    --grid:    #2c2c2a;
    --border:  rgba(255,255,255,0.10);
    --wash:    rgba(255,255,255,0.05);
    --seq-fill:  #3987e5;
    --seq-track: #184f95;
    --stage-0: #9ec5f4; --stage-1: #6da7ec; --stage-2: #3987e5;
    --stage-3: #256abf; --stage-4: #184f95;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19;
  --page:    #0d0d0d;
  --ink-1:   #ffffff;
  --ink-2:   #c3c2b7;
  --ink-3:   #898781;
  --grid:    #2c2c2a;
  --border:  rgba(255,255,255,0.10);
  --wash:    rgba(255,255,255,0.05);
  --seq-fill:  #3987e5;
  --seq-track: #184f95;
  --stage-0: #9ec5f4; --stage-1: #6da7ec; --stage-2: #3987e5;
  --stage-3: #256abf; --stage-4: #184f95;
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: 28px 22px 60px;
  background: var(--page); color: var(--ink-1);
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei",
               "PingFang SC", "Hiragino Sans GB", sans-serif;
  font-size: 14px; line-height: 1.55;
}
.wrap { max-width: 1500px; margin: 0 auto; }

header { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }
h1 { font-size: 21px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.stamp { color: var(--ink-3); font-size: 12.5px; }
.spacer { flex: 1; }
button { font: inherit; cursor: pointer; }

.theme-btn {
  background: var(--surface); color: var(--ink-2);
  border: 1px solid var(--border); border-radius: 7px;
  padding: 5px 11px; font-size: 12.5px;
}
.theme-btn:hover { background: var(--wash); }

/* ---------- KPI 统计卡 ---------- */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr)); gap: 12px; margin-bottom: 22px; }
.kpi {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 11px; padding: 15px 17px;
}
.kpi .label { color: var(--ink-2); font-size: 12.5px; margin-bottom: 5px; }
.kpi .value { font-size: 30px; font-weight: 600; line-height: 1.1; letter-spacing: -0.02em; }
.kpi .sub { color: var(--ink-3); font-size: 12px; margin-top: 3px; }

/* ---------- 进度条 ---------- */
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 11px; padding: 17px 19px; margin-bottom: 22px; }
.panel h2 { font-size: 13.5px; font-weight: 600; margin: 0 0 13px; color: var(--ink-2); }

.bar { display: flex; height: 26px; border-radius: 5px; overflow: hidden; background: var(--grid); }
.bar .seg { position: relative; min-width: 0; transition: opacity .12s; }
.bar .seg + .seg { border-left: 2px solid var(--surface); }   /* 2px 表面间隙分隔相邻色块 */
.bar .seg:hover { opacity: .82; }
.bar .seg .inlabel {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; color: #fff; font-variant-numeric: tabular-nums;
}
/* 段内文字按填充色明度选墨色 —— 前两档是浅蓝，白字对比度不够 */
.bar .seg[data-s="0"] .inlabel, .bar .seg[data-s="1"] .inlabel { color: #0b0b0b; }
.bar .seg[data-s="0"] { background: var(--stage-0); }
.bar .seg[data-s="1"] { background: var(--stage-1); }
.bar .seg[data-s="2"] { background: var(--stage-2); }
.bar .seg[data-s="3"] { background: var(--stage-3); }
.bar .seg[data-s="4"] { background: var(--stage-4); }

.legend { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; }
.legend .item { display: flex; align-items: center; gap: 7px; color: var(--ink-2); font-size: 12.5px; }
.legend .swatch { width: 11px; height: 11px; border-radius: 3px; flex: none; }
.legend .n { color: var(--ink-1); font-weight: 600; font-variant-numeric: tabular-nums; }

/* ---------- 选项卡 ---------- */
.tabs { display: flex; gap: 3px; margin-bottom: 15px; border-bottom: 1px solid var(--border); }
.tab {
  background: none; border: none; border-bottom: 2px solid transparent;
  padding: 9px 15px; color: var(--ink-2); font-size: 14px; margin-bottom: -1px;
}
.tab:hover { color: var(--ink-1); }
.tab.on { color: var(--ink-1); font-weight: 600; border-bottom-color: var(--seq-fill); }
.tab .cnt { color: var(--ink-3); font-size: 12px; font-variant-numeric: tabular-nums; margin-left: 5px; }

/* ---------- 筛选行 ---------- */
.filters { display: flex; gap: 9px; flex-wrap: wrap; align-items: center; margin-bottom: 13px; }
.filters input[type=search] {
  flex: 1; min-width: 190px; padding: 7px 11px; font: inherit;
  background: var(--surface); color: var(--ink-1);
  border: 1px solid var(--border); border-radius: 7px;
}
.filters input[type=search]::placeholder { color: var(--ink-3); }
.chk { display: flex; align-items: center; gap: 6px; color: var(--ink-2); font-size: 13px; cursor: pointer; user-select: none; }
.chk input { accent-color: var(--seq-fill); }

/* ---------- 表格 ---------- */
.tablewrap { background: var(--surface); border: 1px solid var(--border); border-radius: 11px; overflow: auto; max-height: 78vh; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 9px 13px; text-align: left; border-bottom: 1px solid var(--grid); vertical-align: middle; }
thead th {
  position: sticky; top: 0; z-index: 2;
  background: var(--surface); color: var(--ink-2);
  font-size: 12.5px; font-weight: 600; white-space: nowrap;
  border-bottom: 1px solid var(--border);
}
thead th.sortable { cursor: pointer; user-select: none; }
thead th.sortable:hover { color: var(--ink-1); }
thead th .arrow { color: var(--seq-fill); margin-left: 3px; }
tbody tr:hover { background: var(--wash); }
tbody tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }

.company { font-weight: 600; }
.company .new { color: var(--st-critical); font-size: 10.5px; font-weight: 700; margin-left: 5px; vertical-align: 1px; }
.position { color: var(--ink-2); max-width: 430px; }
.sub2 { color: var(--ink-3); font-size: 12px; }

/* 匹配分：顺序色阶的量表 */
.meter { display: flex; align-items: center; gap: 9px; justify-content: flex-end; }
.meter .track { width: 54px; height: 7px; border-radius: 3.5px; background: var(--seq-track); overflow: hidden; flex: none; }
/* display:block 是必需的 —— span 默认行内，width 不生效，填充条会消失 */
.meter .fill { display: block; height: 100%; border-radius: 3.5px; background: var(--seq-fill); }
.meter .val { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 30px; text-align: right; }

/* 截止：状态色只落在圆点上，文字用文本色 —— 颜色不单独表意 */
.dl { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-size: 12.5px; }
.dl .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.dl .dot.critical { background: var(--st-critical); }
.dl .dot.serious  { background: var(--st-serious); }
.dl .dot.warning  { background: var(--st-warning); }
.dl .dot.normal   { background: var(--st-good); }
.dl .dot.none     { background: var(--ink-3); }
.dl.critical { color: var(--ink-1); font-weight: 600; }
.dl.hit { background: color-mix(in srgb, var(--st-critical) 13%, transparent); padding: 1px 8px 1px 7px; border-radius: 20px; }
.dl .icon { font-size: 11px; }

a.go {
  color: var(--seq-fill); text-decoration: none; font-size: 12.5px;
  border-bottom: 1px solid transparent; white-space: nowrap;
}
a.go:hover { border-bottom-color: currentColor; }
.nolink { color: var(--ink-3); font-size: 12.5px; }

.empty { padding: 42px; text-align: center; color: var(--ink-3); }
.hide { display: none !important; }
footer { margin-top: 26px; color: var(--ink-3); font-size: 12px; line-height: 1.8; }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <h1>2027 届秋招看板</h1>
    <span class="stamp">更新于 <span id="stamp"></span></span>
    <span class="spacer"></span>
    <button class="theme-btn" id="themeBtn">切换深色</button>
  </header>

  <div class="kpis" id="kpis"></div>

  <div class="panel">
    <h2>投递进度</h2>
    <div class="bar" id="bar"></div>
    <div class="legend" id="legend"></div>
  </div>

  <div class="tabs">
    <button class="tab on" data-tab="tech">技术岗 <span class="cnt" id="cntTech"></span></button>
    <button class="tab"    data-tab="soe">国央企 <span class="cnt" id="cntSoe"></span></button>
  </div>

  <div class="filters">
    <input type="search" id="q" placeholder="搜索公司或岗位…">
    <label class="chk"><input type="checkbox" id="onlyOpen"> 只看未截止</label>
    <label class="chk"><input type="checkbox" id="onlyNew"> 只看新增</label>
  </div>

  <div class="tablewrap">
    <table>
      <thead><tr id="thead"></tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    <div class="empty hide" id="empty">没有符合条件的岗位</div>
  </div>

  <footer>
    数据来自公开的校招信息聚合源（校招雷达 / xixicc2027）与国聘网、国家大学生就业服务平台，仅供线索参考；
    投递前请以公司官网为准。截止日期为源数据标注，可能变动。<br>
    状态更新：运行 <code>python scripts/track.py 公司名 状态</code> 即可写入数据库。
  </footer>
</div>

<script>
const DATA = __DATA__;
const STAGE_KEYS = DATA.stages;

document.getElementById('stamp').textContent = DATA.generated;

/* ---------- KPI ---------- */
const s = DATA.stats;
const KPI = [
  ['岗位总数', s.total, `技术岗 ${s.tech} · 国央企 ${s.soe}`],
  ['今日新增', s.new,  '首次抓取到的岗位'],
  ['待投递',   s.pending, `面试中 ${s.interviewing}`],
  ['临近截止', s.urgent,  `仅统计对口岗位（共 ${s.urgentAll} 条临近，已滤掉不对口的）`],
];
document.getElementById('kpis').innerHTML = KPI.map(([label, value, sub]) =>
  `<div class="kpi"><div class="label">${label}</div>
   <div class="value">${value.toLocaleString()}</div>
   <div class="sub">${sub}</div></div>`).join('');

/* ---------- 进度条 ---------- */
const total = DATA.funnel.reduce((a, b) => a + b.count, 0) || 1;
document.getElementById('bar').innerHTML = DATA.funnel.map((f, i) => {
  const pct = f.count / total * 100;
  if (pct <= 0) return '';
  const wide = pct >= 7;
  return `<div class="seg" data-s="${i}" style="width:${pct}%" title="${f.stage}：${f.count}">
            ${wide ? `<span class="inlabel">${f.count}</span>` : ''}</div>`;
}).join('');
document.getElementById('legend').innerHTML = DATA.funnel.map((f, i) =>
  `<span class="item"><span class="swatch" style="background:var(--stage-${i})"></span>
   ${f.stage} <span class="n">${f.count}</span></span>`).join('');

/* ---------- 表格 ---------- */
const COLS = {
  tech: [
    { key: 'score',    label: '匹配',  num: true,  sortable: true },
    { key: 'company',  label: '公司',  sortable: true },
    { key: 'position', label: '岗位',  sortable: false },
    { key: 'place',    label: '城市',  sortable: false },
    { key: 'nature',   label: '批次',  sortable: false },
    { key: 'deadline', label: '截止',  sortable: true },
    { key: 'url',      label: '',      sortable: false },
  ],
  soe: [
    { key: 'score',     label: '匹配',  num: true,  sortable: true },
    { key: 'company',   label: '公司',  sortable: true },
    { key: 'position',  label: '岗位',  sortable: false },
    { key: 'place',     label: '地区',  sortable: false },
    { key: 'nature',    label: '性质',  sortable: false },
    { key: 'education', label: '学历',  sortable: false },
    { key: 'deadline',  label: '截止',  sortable: true },
    { key: 'url',       label: '',      sortable: false },
  ],
};

let tab = 'tech';
let sortKey = 'score';
let sortDir = -1;

const DL_ICON = { critical: '⚠', serious: '⚠', warning: '○', normal: '○', none: '—' };

function deadlineCell(r) {
  if (r.days === null || r.days === undefined) {
    return `<span class="dl"><span class="dot none"></span>
            <span>${r.deadline || '招满即止'}</span></span>`;
  }
  const d = r.days;
  let text;
  if (d < 0) text = `已截止 ${-d} 天`;
  else if (d === 0) text = '今天截止';
  else text = `剩 ${d} 天`;
  const hit = r.level === 'critical' ? ' hit' : '';
  return `<span class="dl ${r.level}${hit}">
            <span class="dot ${r.level}"></span>
            <span class="icon">${DL_ICON[r.level] || ''}</span>
            <span>${text}</span></span>`;
}

function scoreCell(r) {
  const max = 80;                       // 量表上限，用于把分数映射到条长
  const w = Math.max(0, Math.min(1, r.score / max)) * 100;
  return `<div class="meter">
            <span class="track"><span class="fill" style="width:${w}%"></span></span>
            <span class="val">${r.score.toFixed(0)}</span>
          </div>`;
}

function rowsFor(t) { return t === 'tech' ? DATA.tech : DATA.soe; }

function render() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const onlyOpen = document.getElementById('onlyOpen').checked;
  const onlyNew  = document.getElementById('onlyNew').checked;

  let rows = rowsFor(tab).filter(r => {
    if (onlyOpen && r.days !== null && r.days < 0) return false;
    if (onlyNew && !r.is_new) return false;
    if (q && !((r.company + ' ' + r.position).toLowerCase().includes(q))) return false;
    return true;
  });

  rows.sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (sortKey === 'deadline') {
      x = a.days === null ? 1e9 : a.days;
      y = b.days === null ? 1e9 : b.days;
    }
    if (typeof x === 'string') return sortDir * x.localeCompare(y, 'zh');
    return sortDir * (x - y);
  });

  // 表头
  document.getElementById('thead').innerHTML = COLS[tab].map(c => {
    const arrow = (c.sortable && sortKey === c.key) ? `<span class="arrow">${sortDir < 0 ? '↓' : '↑'}</span>` : '';
    const cls = (c.num ? 'num ' : '') + (c.sortable ? 'sortable' : '');
    return `<th class="${cls}" ${c.sortable ? `data-k="${c.key}"` : ''}>${c.label}${arrow}</th>`;
  }).join('');

  // 表体
  document.getElementById('tbody').innerHTML = rows.map(r => {
    const cells = COLS[tab].map(c => {
      switch (c.key) {
        case 'score':
          return `<td class="num">${scoreCell(r)}</td>`;
        case 'company':
          return `<td class="company">${esc(r.company)}${r.is_new ? '<span class="new">NEW</span>' : ''}</td>`;
        case 'position':
          return `<td class="position">${esc(r.position) || '<span class="sub2">—</span>'}</td>`;
        case 'place':
          return `<td>${esc(r.place) || '<span class="sub2">—</span>'}</td>`;
        case 'nature':
          return `<td class="sub2">${esc(r.nature) || '—'}</td>`;
        case 'education':
          return `<td class="sub2">${esc(r.education) || '—'}</td>`;
        case 'deadline':
          return `<td>${deadlineCell(r)}</td>`;
        case 'url':
          return `<td>${r.url ? `<a class="go" href="${esc(r.url)}" target="_blank" rel="noopener">投递 ↗</a>` : '<span class="nolink">—</span>'}</td>`;
        default:
          return '<td></td>';
      }
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  document.getElementById('empty').classList.toggle('hide', rows.length > 0);
  const tbl = document.querySelector('table');
  tbl.style.display = rows.length ? '' : 'none';
}

function esc(t) {
  return String(t == null ? '' : t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

document.getElementById('thead').addEventListener('click', e => {
  const th = e.target.closest('th[data-k]');
  if (!th) return;
  const k = th.dataset.k;
  if (sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = (k === 'company') ? 1 : -1; }
  render();
});

document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  tab = b.dataset.tab;
  sortKey = 'score'; sortDir = -1;
  render();
}));

['q', 'onlyOpen', 'onlyNew'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener(id === 'q' ? 'input' : 'change', render);
});

/* ---------- 主题 ---------- */
const root = document.documentElement;
const btn = document.getElementById('themeBtn');
// ?theme=dark / ?theme=light 可强制指定（便于截图与分享），否则用本地记忆
const urlTheme = new URLSearchParams(location.search).get('theme');
const saved = urlTheme || localStorage.getItem('jh-theme');
if (saved) root.setAttribute('data-theme', saved);
else root.removeAttribute('data-theme');

function syncBtn() {
  const dark = root.getAttribute('data-theme') === 'dark'
    || (!root.getAttribute('data-theme') && matchMedia('(prefers-color-scheme: dark)').matches);
  btn.textContent = dark ? '切换浅色' : '切换深色';
}
btn.addEventListener('click', () => {
  const dark = root.getAttribute('data-theme') === 'dark'
    || (!root.getAttribute('data-theme') && matchMedia('(prefers-color-scheme: dark)').matches);
  const next = dark ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  localStorage.setItem('jh-theme', next);
  syncBtn();
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncBtn);
syncBtn();

document.getElementById('cntTech').textContent = DATA.stats.tech;
document.getElementById('cntSoe').textContent = DATA.stats.soe;
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
