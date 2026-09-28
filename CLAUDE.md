# CLAUDE.md

给 AI 编码助手看的架构与约束说明。任务导向的使用说明在 `README.md`。

---

## 三条不可违背的约定

### 1. 简历内容必须可溯源到 `data/resume/facts.yaml`

生成简历时只允许「**选择、排序、改写措辞**」，**绝不允许引入 facts.yaml 里没有的事实**
（新的项目、夸大的指标、没做过的技术）。

这是本项目最重要的一条规则。简历造假在面试追问和入职背调阶段会直接出局，
而 AI 生成简历最容易犯的错就是「顺手补一个合理的数字」。

**如果某个岗位需要 facts.yaml 里没有的能力，正确做法是告诉用户这个 gap，
而不是替他补上。** `build_resume.py --jd` 的缺口分析就是这条规则的执行机制。

### 2. 任何「提交 / 发送」动作都由用户点击

AI 可以聚合、排序、生成草稿、填表，但**不得点击提交按钮、不得发送邮件**。
`apply/autofill.js` 生成的脚本绝不能包含任何 click/submit 逻辑。

### 3. 技术岗与国央企严格分开

不同的源、不同的表（`tech_jobs` / `soe_jobs`）、不同的报告分区。
混在一起排序会让国央企岗位被互联网岗位淹没。

---

## 架构

**SQLite 是唯一真相源**：`data/jobs.db`。所有环节读写同一个库，进度追踪因此天然集成。
`data/raw/YYYY-MM-DD/` 保留每日原始快照，用于回溯，也是数据源失效时迁移的依据。

```
scripts/common.py           路径、数据库 schema、HTTP、快照、去重 id —— 共享工具
scripts/fetch_tech.py       技术岗抓取（社区聚合源）
scripts/fetch_soe.py        国央企抓取（国聘网 + 国家大学生就业服务平台）
scripts/score.py            匹配打分，权重全部来自 config.yaml
scripts/dashboard.py        生成 dashboard.html（单文件、离线、浅深色）
scripts/daily_list.py       把全量收敛成可执行的短名单
scripts/build_resume.py     简历生成 + PDF 导出 + JD 缺口分析
scripts/build_autofill.py   从 facts.yaml 生成网申填表书签
scripts/build_questions.py  面试题库 + 复盘回流
scripts/track.py            投递进度追踪
scripts/run_daily.py        串起全流程，供定时任务调用
```

功能脚本统一用 `sys.path.insert(0, ...)` + `from common import ...` 引用共享工具。

---

## 已经踩过的坑（改代码前先读这段）

### 数据源

- **国聘网是「推荐池」接口，不是稳定列表。** 分页重叠（实测 page1∩page2 ≈ 5 条），
  每次返回不同切片。靠每日重跑累积覆盖 —— **这不是 bug，不要"修复"它**。
  `upsert_jobs()` 的 `first_seen` / `last_seen` 机制就是为此设计的。
- **`page_size` 调大反而拿得少。** 实测 20 → 400 槽位；50 → 只返回 140 条。
- **国聘的 `min_wage`/`max_wage` 严重低估实际待遇**（实测挂网 8K，官方公告 15-26K）。
  不要基于这两个字段给用户做薪资判断。
- **NCSS 官方文档称每查询 100 条，实测只有 20 条**，且 `offset` 无效。代码按实际处理。
- **聚合源里「一条记录打包一批不相关岗位」** —— 只要一两个词命中就整条高分。
  高分不等于对口。

### 打分

- **同义词必须取最高值，不能累加。** `major_weights` 里「计算机类、计算机科学与技术、
  软件工程」是同一件事的三种写法，累加会让消防管理岗凭空多出 58 分顶到前排。
  学历同理（「本科及以上」包含「本科」）。见 `_best()`。
- **国央企的 JD 正文折算 0.4 权重。** 正文越长越容易堆砌技术名词，
  不折算会让「JD 写得长」的岗位无脑排前。
- **负向词只看岗位名，不看 JD 正文。** 正文里出现「运营」「财务」多是描述协作方。
- **国央企有大量「要求计算机专业、但岗位不是技术岗」的职位**，靠负向词压下去。

### 简历

- **PDF 用 Edge 的 `--print-to-pdf`，不需要 Playwright。** 两个坑：
  必须加 `--user-data-dir` 指向独立临时目录（否则 Edge 检测到已有实例会转交任务并
  立即返回，什么都不打印）；**进程退出 ≠ 文件写完**，必须轮询等待落盘。
- **校招简历压进 1 页。** 溢出的永远是最低价值的内容 —— 优先砍 `campus_n` /
  `compact_n`，不要为了塞内容把字号调到 9pt 以下。
- **同义词/分类要准确。** Scikit-Learn 是传统机器学习库不是深度学习框架；
  BERT 是 encoder-only 预训练模型，和 LLaMA/Qwen 并列在「大模型」下是硬凑，
  技术面试官一眼能看出。

### 填表书签

- **纯 ASCII 键必须做词边界匹配。** `name="schoolName"` 里的 "name" 会命中「姓名」规则，
  而姓名已填过 → 整个学校字段被跳过。用 `(^|[^a-z])` 前缀断言解决。
- **匹配到已填字段时要继续尝试次优规则**，不能直接 `continue` 丢掉整个控件。
- **表格布局里标签在相邻 `<td>`**，而 `closest('td')` 取到的是装 input 的空格子 ——
  容器文字为空时要退回整行 `<tr>`。
- **React/Vue 受控组件必须走原生 setter 再派发 `input`/`change` 事件**，
  直接 `el.value = x` 会被框架状态覆盖回去。

### 其他

- **`.bat` 文件必须纯 ASCII + CRLF。** cmd.exe 按 OEM 代码页读取，UTF-8 中文注释
  会被误解析成命令，导致脚本**静默失败且无任何报错**。
- **YAML 里以 `*` `&` `!` `%` `@` 反引号开头的裸值会被当成别名/锚点报错**；
  以引号开头的列表项会被当成字符串起始符。用 `>-` 折叠块标量包一层。
  `interview/questions.yaml` 被这个坑绊过两次。
- **`track.py` 的 `find()` 必须返回 `job_id`。** 同一公司常有多条记录，
  若下游按 `company` 反查再用 `fetchone()`，会把同一条反复更新、其余永远改不到，
  而且表面还打印「已更新 3 条」，很有迷惑性。

---

## 隐私

项目会生成含真实姓名、手机、邮箱、学号、完整简历的文件。`.gitignore` 已排除，
但**任何涉及提交的操作前，都要先扫一遍**。

把真实标识填进 `IDENT` 再跑：

```python
import pathlib, subprocess

IDENT = {                      # 换成实际值
    '姓名': 'xxx', '手机': '1xxxxxxxxxx', '邮箱': 'x@x.com',
    '学号': 'xxxxxxxxx', '学校': 'xxx大学', '本机路径': r'C:\Users\<用户名>',
}

files = subprocess.run(['git', 'diff', '--cached', '--name-only'],
                       capture_output=True, text=True).stdout.split()
for f in files:
    raw = open(f, 'rb').read()          # 用二进制读，图片也能扫
    hit = [k for k, v in IDENT.items() if v.encode() in raw]
    if hit:
        print(f'[!!] {f}: {", ".join(hit)}')
```

**提交前扫、推送后再从远端拉一遍复核** —— 本地干净不代表线上干净
（比如忘了 `git add` 某个文件、或者 `.gitignore` 写漏了一条）。

`data/resume/facts.yaml`、`resume/output/`、`apply/autofill.js`、`data/jobs.db`
是最高风险的四个路径。
