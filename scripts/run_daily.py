"""每日流水线：抓取 → 打分 → 生成看板。供 Windows 任务计划程序调用。

每个步骤独立成败：某一源挂了不影响后续步骤。全过程只读公开数据，
低频串行请求，不绕过任何验证。

用法：
    python scripts/run_daily.py          # 跑一次
    python scripts/run_daily.py --quiet  # 只写日志，不打印
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG_DIR = ROOT / "data" / "logs"

STEPS = [
    ("fetch_tech.py", "抓取技术岗"),
    ("fetch_soe.py",  "抓取国央企"),
    ("score.py",      "匹配打分"),
    ("dashboard.py",  "生成看板"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    log_path = LOG_DIR / f"{datetime.now():%Y-%m}.log"

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    say(f"\n{'=' * 60}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 每日流水线开始")

    failed: list[str] = []
    for script, label in STEPS:
        say(f"\n--- {label}（{script}）---")
        try:
            proc = subprocess.run(
                [sys.executable, str(HERE / script)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900, cwd=str(ROOT),
            )
        except subprocess.TimeoutExpired:
            say(f"[X] {label} 超时（15 分钟）")
            failed.append(label)
            continue

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if out:
            say(out)
        if proc.returncode != 0:
            say(f"[X] {label} 退出码 {proc.returncode}")
            if err:
                say(err[-1500:])
            failed.append(label)

    say(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] 流水线结束"
        + (f"；失败：{', '.join(failed)}" if failed else "；全部成功"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
