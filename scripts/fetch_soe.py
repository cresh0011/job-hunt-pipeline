"""国央企抓取：国聘网 + 国家大学生就业服务平台 → jobs.db 的 soe_jobs 表。

与 fetch_tech.py 严格分离：不同的源、不同的表、不同的报告。
本表独有的价值是 end_time（报名截止时间）—— 国央企最容易踩的坑。

用法：
    python scripts/fetch_soe.py
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    connect, fix_stdout, http_get_json, http_post_json, load_config,
    save_snapshot, upsert_jobs,
)


# ---------------------------------------------------------------- 国聘网


def fetch_iguopin(src: dict) -> list[dict]:
    """国聘网推荐池。实测 total=400，按 page_size 分页取回。

    接口是 POST + JSON body；Referer 必须带，否则可能被拒。
    """
    api = src["api"]
    headers = {"Referer": src.get("referer", "https://www.iguopin.com/job/list")}
    page_size = src.get("page_size", 20)
    max_pages = src.get("max_pages", 25)

    raw: list[dict] = []
    for page in range(1, max_pages + 1):
        body = {
            "search": {"page": page, "page_size": page_size},
            "recom": {"update_time": True, "company_nature": True, "hot_job": True},
        }
        try:
            resp = http_post_json(api, body, headers=headers)
        except Exception as exc:  # noqa: BLE001
            print(f"    国聘 page={page} 失败：{exc}")
            break

        payload = resp.get("data") or {}
        batch = payload.get("list") or []
        if not batch:
            break

        raw.extend(batch)
        total = payload.get("total") or 0
        print(f"    国聘 page={page}：+{len(batch)}（累计 {len(raw)}/{total}）")

        if total and len(raw) >= total:
            break

    save_snapshot("iguopin", raw)
    return [_map_iguopin(x) for x in raw if _is_campus(x)]


def _is_campus(item: dict) -> bool:
    """只保留校园招聘岗位。"""
    return (item.get("recruitment_type_cn") or "").strip() == "校园招聘"


def _map_iguopin(item: dict) -> dict:
    job_id = str(item.get("job_id") or "").strip()
    company = (item.get("company_name") or "").strip()

    # 地区：district_list 里取前若干个 area_cn
    districts = [
        (d.get("area_cn") or "").strip()
        for d in (item.get("district_list") or [])
        if (d.get("area_cn") or "").strip()
    ]
    district_cn = "、".join(dict.fromkeys(districts))  # 去重且保序

    # 薪资：min/max + 单位
    lo = item.get("min_wage") or 0
    hi = item.get("max_wage") or 0
    unit = (item.get("wage_unit_cn") or "").strip()
    if item.get("is_negotiable") or (not lo and not hi):
        wage = "面议"
    elif lo and hi and lo != hi:
        wage = f"{lo}-{hi} {unit}"
    else:
        wage = f"{lo or hi} {unit}"

    # 公司性质优先取 company_info.nature_cn（"国企"/"事业单位"），退化到岗位的 nature_cn
    info = item.get("company_info") or {}
    nature_cn = (info.get("nature_cn") or item.get("nature_cn") or "").strip()

    # 专业要求 —— 判断「计算机类」是否在列，是匹配打分的重要依据
    majors = [m.strip() for m in (item.get("major_cn") or []) if str(m).strip()]
    majors = list(dict.fromkeys(majors))

    return {
        "job_id": f"iguopin_{job_id}" if job_id else "",
        "company": company,
        "job_name": (item.get("job_name") or "").strip(),
        "nature_cn": nature_cn,
        "education_cn": (item.get("education_cn") or "").strip(),
        "district_cn": district_cn,
        "amount": item.get("amount") or 0,
        "wage": wage,
        "end_time": (item.get("end_time") or "").strip(),
        # 接口不返回详情页链接，用 job_id 拼官方详情页
        "apply_url": f"https://www.iguopin.com/job/detail?id={job_id}" if job_id else "",
        "source": "国聘网",
        # 专业要求与完整 JD —— score.py 用来做匹配打分，tailor.py 用来定制简历
        "majors": "、".join(majors),
        "jd": (item.get("contents") or "").strip(),
    }


# ---------------------------------------------------------------- NCSS


def fetch_ncss(src: dict) -> list[dict]:
    """国家大学生就业服务平台（补充源，贡献有限）。

    实测限制（2026-09-27）：匿名访问每查询实际只返回 20 条，官方文档所称的
    "每查询上限 100 条" 不符；offset 参数不生效（offset=20 起一律返回空）；
    property 除「国有企业」外其余取值均返回空。故只做单次查询，不做分页循环。

    返回的多是校级招聘公告（如「某银行 2027 年全球校园招聘公告」），
    价值在于提示"某国央企校招已开启"，而非具体岗位。
    """
    api = src["api"]
    limit = src.get("page_size", 20)

    raw: list[dict] = []
    for prop in src.get("properties", []):
        qs = {
            "jobType": "", "areaCode": "", "jobName": "", "monthPay": "",
            "industrySectors": "", "property": prop, "categoryCode": "",
            "memberLevel": "", "recruitType": "", "offset": 0,
            "limit": limit, "keyUnits": "", "degreeCode": "",
            "sourcesName": "0", "sourcesType": "",
        }
        url = api + "?" + urllib.parse.urlencode(qs)
        try:
            resp = http_get_json(url, headers={"Referer": "https://ncss.cn/student/jobs/"})
        except Exception as exc:  # noqa: BLE001
            print(f"    NCSS [{prop}] 失败：{exc}")
            continue

        batch = (resp.get("data") or {}).get("list") or []
        raw.extend(batch)
        print(f"    NCSS [{prop}]：+{len(batch)}（该源匿名访问上限，无法翻页）")

    save_snapshot("ncss", raw)
    return [_map_ncss(x) for x in raw]


def _map_ncss(item: dict) -> dict:
    job_id = str(item.get("jobId") or "").strip()
    lo = item.get("lowMonthPay") or 0
    hi = item.get("highMonthPay") or 0
    if lo and hi and lo != hi:
        wage = f"{lo}-{hi} 千元/月"
    else:
        wage = f"{lo or hi} 千元/月" if (lo or hi) else "面议"

    return {
        "job_id": f"ncss_{job_id}" if job_id else "",
        "company": (item.get("recName") or "").strip(),
        "job_name": (item.get("jobName") or "").strip(),
        "nature_cn": (item.get("recProperty") or "").strip(),
        "education_cn": (item.get("degreeName") or "").strip(),
        "district_cn": (item.get("areaCodeName") or "").strip(),
        "amount": item.get("headCount") or 0,
        "wage": wage,
        "end_time": "",
        "apply_url": f"https://job.ncss.cn/student/jobs/{job_id}/detail.html" if job_id else "",
        "source": "国家大学生就业服务平台",
        "majors": (item.get("major") or "").strip(),
        "jd": "",
    }


# ---------------------------------------------------------------- 主流程


def main() -> int:
    fix_stdout()
    cfg = load_config()
    conn = connect()

    total_new = total_upd = 0

    for src in cfg["sources"]["soe"]:
        name = src["name"]
        print(f"[{name}]")

        try:
            if name == "iguopin":
                rows = fetch_iguopin(src)
            elif name == "ncss":
                rows = fetch_ncss(src)
            else:
                print(f"    [X] 未知源 {name}")
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"    [X] 失败：{exc}")
            continue

        # 丢掉仅供打分的临时字段，再入库
        clean = [{k: v for k, v in r.items() if not k.startswith("_") and v != ""} for r in rows]
        clean = [r for r in clean if r.get("job_id")]

        new, upd = upsert_jobs(conn, "soe_jobs", clean)
        total_new += new
        total_upd += upd
        print(f"    [OK] 校招岗位 {len(clean)} 条 -> 新增 {new}，更新 {upd}")

    count = conn.execute("SELECT COUNT(*) FROM soe_jobs").fetchone()[0]
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM soe_jobs WHERE end_time >= date('now')"
    ).fetchone()[0]
    print(f"\n完成：本次新增 {total_new}，更新 {total_upd}")
    print(f"soe_jobs 共 {count} 条，其中 {upcoming} 条尚未截止")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
