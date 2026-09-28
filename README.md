# job-hunt-pipeline

校园招聘求职流水线 —— 岗位聚合、匹配打分、简历定制、投递追踪、面试复盘，全链路本地化。

**零付费 · 零爬虫 · 人在环中 · 数据不出本机**

> 面向中国校招场景（技术岗 + 国央企双轨），从社区聚合数据源拉取岗位，
> 用可配置权重打分排序，按方向变体生成单页简历，并提供填表书签与面试题库。
>
> An offline-first job-hunting pipeline for Chinese campus recruiting.
> No paid APIs, no scraping of job platforms, no data leaving your machine.

---

## 它解决什么问题

校招期间最耗时的不是面试，是**信息处理**：

| 痛点 | 这个项目怎么处理 |
|---|---|
| 每天花 1-2 小时刷招聘网站 | 每日定时抓取 2600+ 岗位，生成可筛选看板 |
| 容易漏掉国央企的报名截止日期 | 国央企独立通道，按截止日期排序并标红 |
| 每家都要改简历，改到崩溃 | 主简历 + 方向变体 + 按 JD 自动微调 |
| 网申表单反复填同样的字段 | 浏览器书签一键填充，跑在自己的会话里 |
| 面试被追问细节答不上来 | 根据你的经历自动生成深挖题库 + 追问链 |
| 面完就忘，同样的坑反复踩 | 复盘回流，题库随面试次数累积 |

![看板](docs/dashboard.png)

---

## 四条核心设计

这个项目的价值不只在功能，更在几个刻意的取舍。

### 一、不爬招聘平台，消费社区聚合数据

自己写 Playwright 爬 Boss直聘 / 牛客网，意味着**高封号风险 + 持续维护成本 + 随时失效**。

社区已经有人维护高质量的校招聚合 JSON（每日更新）。读它们是零风险、零成本、无封号的。
本项目只读公开的 GitHub 原始文件与公开招聘 API。

### 二、防编造：事实锁死在结构化文件里

AI 生成简历最容易犯的错，是顺手补一个"合理"的数字。

所以本项目的简历内容**只能来自 `data/resume/facts.yaml`**，生成脚本只允许
「选择、排序、改写措辞」，不允许引入新事实。

配套的 `--jd` 模式会报出 **JD 要求但事实库没有的能力**：

```
已覆盖（13）：大模型、LLM、Agent、智能体、RAG、检索增强、微调、LoRA...
缺口（1）：向量数据库
```

与其替你编一个能力，不如如实告诉你差距在哪。

### 三、自动化止步于「提交」

填表书签会填好所有能识别的字段，**但绝不点击任何按钮**。

这不是技术限制，是设计边界 —— 让 AI 代你提交网申，风险和收益不成比例。

### 四、技术岗与国央企严格分离

两者的招聘节奏、评价标准、信息渠道完全不同，混在一起排序会让国央企岗位
被互联网岗位淹没。所以：**不同的源、不同的表、不同的报告分区**。

---

## 快速开始

**环境要求**：Python 3.10+，Windows / macOS / Linux
（核心功能只用标准库 + `pyyaml`；PDF 导出需要 Edge 或 Chrome，也可跳过）

```bash
git clone https://github.com/<你的用户名>/job-hunt-pipeline.git
cd job-hunt-pipeline

pip install pyyaml            # 唯一必需的三方库
cp data/resume/facts.example.yaml data/resume/facts.yaml
# 编辑 facts.yaml 填你自己的信息

python scripts/run_daily.py   # 抓取 + 打分 + 生成看板
```

打开 `dashboard.html` 即可看到岗位看板。

### 装填表书签

```bash
python scripts/build_autofill.py
```

浏览器打开 `apply/bookmarklet.html`，把按钮拖到书签栏。
在任意网申页面点一下即可填充基础字段。

### 生成简历

```bash
python scripts/build_resume.py --list                     # 看有哪些变体
python scripts/build_resume.py --all --pdf                # 全部生成
python scripts/build_resume.py --jd jd.txt --company X    # 按 JD 定制
```

变体定义在 `resume/variants.yaml`，只控制**呈现**（章节顺序、技能排序、项目详略），
事实全部来自 `facts.yaml`。

### 投递追踪

```bash
python scripts/track.py 某公司 已投递
python scripts/track.py 某公司 一面 --note "问了 RAG 评测"
python scripts/track.py --stats
```

### 面试准备

```bash
python scripts/build_questions.py --company X --role Y
python scripts/build_questions.py --retro X          # 面完生成复盘模板
python scripts/build_questions.py --collect-retro    # 把实战题回流进题库
```

**复盘回流是整套流程里复利最高的一环** —— 每面完一场记一次，
第三次面试的准备质量会明显高于第一次。

---

## 模块一览

| 脚本 | 作用 |
|---|---|
| `fetch_tech.py` | 技术岗：拉取社区聚合源 |
| `fetch_soe.py` | 国央企：国聘网 + 国家大学生就业服务平台 |
| `score.py` | 匹配打分，权重全在 `config.yaml` |
| `dashboard.py` | 生成单文件看板（离线、浅深色自适应） |
| `daily_list.py` | 把全量收敛成可执行的短名单 |
| `build_resume.py` | 简历生成 + PDF 导出 + JD 缺口分析 |
| `build_autofill.py` | 生成网申填表书签 |
| `build_questions.py` | 面试题库 + 复盘回流 |
| `track.py` | 投递进度追踪 |
| `run_daily.py` | 串起全流程，供定时任务调用 |

### 定时执行

```bash
# Windows
schtasks /create /tn "JobHunt-Daily" /tr "D:\path\to\run_daily.bat" /sc DAILY /st 09:00 /f

# macOS / Linux
0 9 * * * cd /path/to/job-hunt-pipeline && python scripts/run_daily.py --quiet
```

> **Windows 提示**：任务计划默认在错过后跳过。加上「错过后尽快补跑」更实用：
> ```powershell
> $t = Get-ScheduledTask -TaskName "JobHunt-Daily"
> $t.Settings.StartWhenAvailable = $true
> Set-ScheduledTask -TaskName "JobHunt-Daily" -Settings $t.Settings
> ```

### 国央企：按待遇排序

```bash
python scripts/daily_list.py --soe-pay
```

放宽专业要求、按薪资排序，并标注 JD 里能识别到的待遇信号（编制 / 落户 / 公积金 / 宿舍）。

> ⚠️ 这个视角**必须人工复核**。放宽专业匹配后会捞进大量「专业范围写得宽、
> 实际是传统工业岗」的职位（值班员、检修工、电解工……）。

---

## 数据源与已知限制

| 源 | 说明 |
|---|---|
| [xiaozhao-radar](https://github.com/jiabaobei/xiaozhao-radar) | 技术岗主源，约 1600 条，含行业标签与投递链接 |
| [xixicc2027](https://github.com/xixicc186/xixicc2027) | 技术岗补充，结构最规整，含批次与截止日期 |
| [国聘网](https://www.iguopin.com/) | 国央企主源，带真实截止时间、完整 JD 与专业要求 |
| [国家大学生就业服务平台](https://ncss.cn/) | 薄补充，匿名访问每查询仅 20 条 |

**几个实测出来的坑**（读代码时不用再踩一遍）：

- **国聘网是「推荐池」接口，不是稳定列表。** 分页会重叠（实测 page1∩page2 ≈ 5 条），
  每次返回的切片不同。靠每日重跑累积覆盖，这不是 bug。
- **国聘的 `min_wage`/`max_wage` 严重低估实际待遇。** 实测某岗位挂网 8K，
  官方校招公告写 15-26K。想看真实薪酬福利，**必须去查公司官方招聘公告**。
- **国家大学生就业服务平台官方文档称「每查询上限 100 条」，实测只有 20 条**，
  且 `offset` 参数完全无效。代码按实际情况处理，没有假装它能分页。
- **聚合源里存在「一条记录打包一批不相关岗位」的情况**，只要其中一两个词命中
  关键词整条就得高分。所以高分不等于对口，**必须点进 JD 确认**。
- **`.bat` 文件必须纯 ASCII + CRLF。** cmd.exe 按 OEM 代码页（中文 Windows 是 GBK）
  读取，UTF-8 中文注释会被误解析成命令，导致脚本**静默失败且无任何报错**。

---

## 设计取舍：为什么没做这些

**为什么不用 MCP？**
调研过市面上的求职类 MCP Server，全部面向 LinkedIn / 俄罗斯 hh.ru / 美国 Handshake，
对中国招聘市场无用。而本项目的岗位数据是批量抓取后落库的，
Claude Code 直接用 Bash 跑脚本读 SQLite，比走 MCP 更简单、更稳、更省 token。
唯一的例外是浏览器自动化（可选装 `@playwright/mcp`）。

**为什么 PDF 用 Edge 的 `--print-to-pdf` 而不是 Playwright？**
Windows 自带 Edge（Chromium 内核），`--print-to-pdf` 渲染质量与 Playwright 一致，
但省掉约 150MB 的浏览器下载。两个必须注意的点：
必须加 `--user-data-dir` 指向独立临时目录（否则 Edge 检测到已有实例会转交任务并立即返回），
且进程退出 ≠ 文件写完，必须轮询等待落盘。

**为什么做过「只填不交」的填表书签，而不做全自动投递？**
大厂网申系统（北森 / MokaHR / 大易 / 自建）DOM 结构各异，写死选择器的维护成本会迅速
超过收益。而书签跑在用户自己的浏览器与登录态里，不引入任何自动化指纹 —— 零封号风险。
大量公司复用同一套 SaaS，所以按**字段语义**匹配而非按选择器硬编码，覆盖率更高。

---

## 目录结构

```
├── config.yaml              匹配权重、数据源、城市/学历/专业偏好（改这里，不用改代码）
├── CLAUDE.md                给 AI 助手看的架构与约束说明
├── run_daily.bat            一键跑全流程（Windows）
├── data/
│   ├── jobs.db              SQLite 主库（唯一真相源）
│   ├── raw/                 每日原始快照
│   └── resume/
│       ├── facts.yaml       事实库 ← 你的简历内容唯一来源
│       └── facts.example.yaml
├── resume/
│   ├── variants.yaml        方向变体定义
│   ├── templates/           Jinja2 简历模板
│   └── output/              生成的 HTML / PDF
├── apply/                   填表书签
├── interview/
│   ├── questions.yaml       题库内容
│   ├── question_bank/       按公司生成的题库
│   └── retro/               面试复盘
└── scripts/                 全部脚本
```

---

## ⚠️ 隐私提醒

这个项目会在本地生成含**真实姓名、手机、邮箱、学号、学校、完整简历**的文件。

仓库自带的 `.gitignore` 已排除这些路径，但**提交前请自己再检查一遍**：

```bash
git status --short
git diff --cached --stat
```

如果你 fork 了这个项目用于自己的求职，**务必确认 `data/resume/facts.yaml`、
`resume/output/`、`apply/autofill.js` 没有被提交**。

---

## License

MIT
