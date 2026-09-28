"""生成网申填表书签：从 facts.yaml 取数据 → apply/autofill.js → apply/bookmarklet.html

为什么用书签而不是爬虫/自动投递：
  · 书签跑在用户自己的浏览器和登录态里，不引入任何自动化指纹，无封号风险
  · 只填不交 —— 提交按钮永远由用户点击（项目硬约束）
  · 零依赖，不需要 Playwright / 浏览器扩展 / 额外进程

针对中文网申系统做了两处必要处理：
  · React/Vue 受控组件必须走原生 setter 再派发 input/change 事件，
    直接 el.value = x 会被框架的状态覆盖回去
  · 大量公司复用同一批 SaaS（北森 / MokaHR / 大易），所以按字段语义匹配
    而不是按选择器硬编码

用法：
    python scripts/build_autofill.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, fix_stdout  # noqa: E402

FACTS_PATH = ROOT / "data" / "resume" / "facts.yaml"
APPLY_DIR = ROOT / "apply"


def build_profile(facts: dict) -> dict:
    """从事实库抽出网申表单要填的字段。

    只放**稳定、可公开、每次都要填**的信息。每次投递都会变的内容
    （期望薪资、应聘岗位、自我介绍）不放，留给用户手填。
    身份证号不在事实库里，也不该预填 —— 属于高敏感信息。
    """
    b = facts["basic"]
    edu = facts["education"][0]
    return {
        "name": b["name"],
        "phone": b["phone"],
        "email": b["email"],
        "school": edu["school"],
        "major": edu["major"],
        "degree": edu["degree"],
        "graduation": "2027-06",            # 网申系统多要求 YYYY-MM
        "graduationYear": "2027",
        "studentId": b.get("student_id", ""),
        "location": b.get("location", ""),
        "hometown": b.get("hometown", ""),
        "github": b.get("github_url", ""),
        # 事实库里没有性别，留空 —— 不猜。需要时用户自己填。
        "gender": "",
    }


JS_TEMPLATE = r"""/* 2027 秋招网申填表书签 —— 由 scripts/build_autofill.py 从 facts.yaml 生成，勿手改 */
(function () {
  'use strict';

  var PROFILE = __PROFILE__;

  /* 字段语义匹配规则。keys 命中字段的上下文（label/placeholder/name/id/周边文本）即填充。
     顺序即优先级：越靠前越先匹配，避免「毕业院校」被「学校」以外的规则抢走。 */
  var RULES = [
    { f: 'name',       keys: ['姓名', '真实姓名', '名字', 'fullname', 'realname', 'username', 'name'] },
    { f: 'phone',      keys: ['手机', '手机号', '移动电话', '联系电话', '联系方式', '电话', 'mobile', 'phone', 'tel'] },
    { f: 'email',      keys: ['邮箱', '电子邮箱', '电子邮件', '常用邮箱', 'email', 'mail'] },
    { f: 'studentId',  keys: ['学号', 'studentid', 'student_no', 'studentno', 'studentnumber', 'stuno', 'stuid'] },
    { f: 'school',     keys: ['毕业院校', '学校', '院校', '就读学校', 'school', 'university', 'college'] },
    { f: 'major',      keys: ['专业名称', '所学专业', '专业', 'major', 'specialty'] },
    { f: 'degree',     keys: ['学历', '学位', '最高学历', 'degree', 'education'] },
    { f: 'graduation', keys: ['毕业时间', '毕业年月', '毕业日期', 'graduation', 'graddate'] },
    { f: 'location',   keys: ['现居住地', '现居地', '当前所在地', '所在城市', '居住地', 'location', 'city'] },
    { f: 'hometown',   keys: ['户籍', '籍贯', '户口所在地', 'hometown'] },
    { f: 'github',     keys: ['github', '个人主页', '技术博客', '博客', 'homepage', 'website'] }
  ];

  /* 这些字段绝不碰：密码、文件上传、验证码、以及任何看起来像提交控件的 */
  var NEVER = /password|passwd|pwd|captcha|verify|code|sms|token|signature|upload|file/i;
  var SUBMIT_WORDS = /submit|提交|保存|发送|确认|下一步|next|save/i;

  function text(el) {
    return (el ? (el.innerText || el.textContent || '') : '').trim();
  }

  /* 收集一个输入框的「语义上下文」：自身属性 + 关联 label + 邻近文本 */
  function contextOf(el) {
    var parts = [
      el.getAttribute('name'), el.getAttribute('id'),
      el.getAttribute('placeholder'), el.getAttribute('aria-label'),
      el.getAttribute('title'), el.getAttribute('data-label')
    ];

    /* label[for=id] */
    if (el.id) {
      try {
        var lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lab) parts.push(text(lab));
      } catch (e) { /* CSS.escape 不被支持时忽略 */ }
    }
    /* 包裹式 label */
    var wrap = el.closest ? el.closest('label') : null;
    if (wrap) parts.push(text(wrap));

    /* 容器文字。表格布局里标签常在**相邻的**单元格，而 closest('td') 取到的是
       装 input 的那个空格子 —— 此时退回整行 <tr> 才拿得到字段名。 */
    var host = el.closest ? el.closest('td, th, .form-item, .field, .ant-form-item') : null;
    var hostText = host ? text(host) : '';
    if (hostText.length < 2) {
      var row = el.closest ? el.closest('tr') : null;
      if (row) hostText = text(row);
    }
    if (hostText) parts.push(hostText.slice(0, 80));

    /* 前一个兄弟节点常是字段名 */
    var prev = el.previousElementSibling;
    if (prev) parts.push(text(prev).slice(0, 40));

    return parts.filter(Boolean).join(' ').toLowerCase();
  }

  var _reCache = {};

  /* 纯 ASCII 键要求前面不是字母：否则 'name' 会命中 'schoolname'，
     'schoolname' 被误判成姓名字段，而姓名已填过 -> 整个学校字段被跳过。 */
  function asciiMatcher(key) {
    if (_reCache[key]) return _reCache[key];
    var esc = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp('(^|[^a-z])' + esc);
    _reCache[key] = re;
    return re;
  }

  function isAscii(key) { return /^[\x00-\x7F]+$/.test(key); }

  /* 返回该控件命中的**全部**规则，按键长降序 —— 越长的键越具体，优先采用。
     不在这里就定下唯一结果：某个字段可能已填过，调用方需要继续尝试次优规则。 */
  function matchAll(ctx) {
    var hits = [];
    for (var i = 0; i < RULES.length; i++) {
      var r = RULES[i];
      if (!PROFILE[r.f]) continue;
      for (var j = 0; j < r.keys.length; j++) {
        var k = r.keys[j].toLowerCase();
        var at = isAscii(k) ? ctx.search(asciiMatcher(k)) : ctx.indexOf(k);
        if (at === -1) continue;
        hits.push({ f: r.f, len: k.length, at: at });
      }
    }
    hits.sort(function (a, b) { return b.len - a.len || a.at - b.at; });
    return hits;
  }

  /* React / Vue 受控组件：直接改 value 会被框架状态覆盖回去。
     必须调原生 setter 再派发事件，让框架感知到变化。 */
  function setValue(el, value) {
    var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  function fillSelect(el, value) {
    var opts = Array.prototype.slice.call(el.options || []);
    var want = String(value).toLowerCase();
    var hit = opts.find(function (o) {
      var t = (o.text || '').trim().toLowerCase();
      return t === want || t.indexOf(want) !== -1;
    });
    if (!hit) return false;
    el.value = hit.value;
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function fillable(el) {
    if (el.disabled || el.readOnly) return false;
    if (NEVER.test(el.type || '') || NEVER.test(el.name || '') || NEVER.test(el.id || '')) return false;
    if (el.offsetParent === null && el.type !== 'hidden') return false;   /* 不可见 */
    var t = (el.tagName || '').toLowerCase();
    if (t !== 'input' && t !== 'textarea' && t !== 'select') return false;
    return true;
  }

  function run() {
    var els = document.querySelectorAll('input, textarea, select');
    var filled = [], skipped = [], seen = {};

    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (!fillable(el)) continue;

      var ctx = contextOf(el);

      /* 取命中规则中第一个尚未填过的字段。注意不能因为首选字段已填过就 continue ——
         那样会把整个控件丢掉（schoolname 误命中 name 规则时就是这个后果）。 */
      var hits = matchAll(ctx);
      var f = null;
      for (var h = 0; h < hits.length; h++) {
        if (!seen[hits[h].f]) { f = hits[h].f; break; }
      }
      if (!f) continue;

      var val = PROFILE[f];
      if (!val) continue;

      var ok;
      if (el.tagName === 'SELECT') {
        ok = fillSelect(el, val);
      } else if (el.value && el.value.trim()) {
        skipped.push(f + '（已有内容，未覆盖）');
        seen[f] = 1;
        continue;
      } else {
        setValue(el, val);
        ok = true;
      }

      if (ok) {
        el.style.outline = '2px solid #0ca30c';
        el.style.outlineOffset = '1px';
        filled.push(f);
        seen[f] = 1;
      }
    }

    report(filled, skipped, els.length);
  }

  function report(filled, skipped, total) {
    var old = document.getElementById('__jh_autofill__');
    if (old) old.remove();

    var box = document.createElement('div');
    box.id = '__jh_autofill__';
    box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483647;' +
      'max-width:330px;padding:13px 15px;border-radius:9px;background:#1a1a19;color:#fff;' +
      'font:13px/1.6 system-ui,-apple-system,"Microsoft YaHei",sans-serif;' +
      'box-shadow:0 6px 26px rgba(0,0,0,.34)';

    var h = '<div style="font-weight:700;margin-bottom:7px">已填充 ' + filled.length + ' 个字段</div>';
    if (filled.length) {
      h += '<div style="color:#c3c2b7;word-break:break-all">' + filled.join('、') + '</div>';
    }
    if (skipped.length) {
      h += '<div style="margin-top:7px;color:#fab219">跳过：' + skipped.join('；') + '</div>';
    }
    h += '<div style="margin-top:9px;color:#c3c2b7">共扫描 ' + total + ' 个控件。' +
         '<b style="color:#fff">请自己核对后手动提交</b> —— 本工具不会点击任何按钮。</div>';
    h += '<div style="margin-top:9px;text-align:right">' +
         '<span id="__jh_close__" style="cursor:pointer;color:#86b6ef">关闭</span></div>';

    box.innerHTML = h;
    document.body.appendChild(box);
    document.getElementById('__jh_close__').onclick = function () { box.remove(); };
    setTimeout(function () { if (box.parentNode) box.remove(); }, 20000);
  }

  run();
})();
"""


def minify(js: str) -> str:
    """去掉整行注释与缩进，让 bookmarklet 短一些。

    只删**独占一行**的 // 注释，不做通用压缩 —— 字符串里出现的 // 不会被误伤。
    """
    out = []
    for line in js.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        out.append(s)
    return "\n".join(out)


def main() -> int:
    fix_stdout()
    import yaml

    with open(FACTS_PATH, encoding="utf-8") as f:
        facts = yaml.safe_load(f)

    profile = build_profile(facts)
    js = JS_TEMPLATE.replace("__PROFILE__", json.dumps(profile, ensure_ascii=False, indent=2))

    APPLY_DIR.mkdir(parents=True, exist_ok=True)

    readable = APPLY_DIR / "autofill.js"
    readable.write_text(js, encoding="utf-8")

    compact = minify(js)
    bookmarklet = "javascript:" + quote(compact, safe="")

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>网申填表书签</title>
<style>
  body {{ font: 14px/1.7 system-ui, -apple-system, "Microsoft YaHei", sans-serif;
         max-width: 720px; margin: 48px auto; padding: 0 22px; color: #1a1a1a; }}
  h1 {{ font-size: 20px; }}
  .bm {{ display: inline-block; padding: 11px 20px; margin: 14px 0; border-radius: 8px;
        background: #1a4d8f; color: #fff; text-decoration: none; font-weight: 600; cursor: grab; }}
  .bm:active {{ cursor: grabbing; }}
  code {{ background: #f2f4f7; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }}
  .note {{ background: #fff8e6; border-left: 3px solid #fab219; padding: 11px 15px; margin: 18px 0; }}
  ol {{ padding-left: 22px; }} li {{ margin: 5px 0; }}
  table {{ border-collapse: collapse; margin-top: 8px; }}
  td {{ border: 1px solid #e1e0d9; padding: 5px 11px; font-size: 13px; }}
  td:first-child {{ color: #52514e; }}
</style></head>
<body>
<h1>网申填表书签</h1>

<p>把下面这个按钮<b>拖到浏览器书签栏</b>。以后在网申页面点一下，就会自动填充基础信息。</p>

<p><a class="bm" href="{bookmarklet.replace('"', '&quot;')}">填写网申表单</a></p>

<div class="note">
  <b>它只填，不交。</b>本工具不会点击任何按钮、不会提交表单、不会上传文件。
  填完请你逐项核对，自己点提交。
</div>

<h2 style="font-size:15px">会用到的信息</h2>
<table>
{"".join(f"<tr><td>{k}</td><td>{v or '（未设置）'}</td></tr>" for k, v in [
    ("姓名", profile["name"]), ("手机", profile["phone"]), ("邮箱", profile["email"]),
    ("学校", profile["school"]), ("专业", profile["major"]), ("学历", profile["degree"]),
    ("毕业时间", profile["graduation"]), ("学号", profile["studentId"]),
    ("现居", profile["location"]), ("户籍", profile["hometown"]),
    ("GitHub", profile["github"]),
])}
</table>

<h2 style="font-size:15px">使用说明</h2>
<ol>
  <li>填不上的字段说明它的命名规则不在已收录的规则里 —— 手动填即可，不影响其他字段。</li>
  <li>已经有内容的字段<b>不会覆盖</b>，避免冲掉你已填好的信息。</li>
  <li>自动填的字段会描一圈绿边，方便你快速核对。</li>
  <li>React/Vue 驱动的表单（北森、MokaHR 等）也能填 —— 已处理受控组件的赋值方式。</li>
  <li>拖拽不方便时，也可以打开 <code>apply/autofill.js</code>，把内容粘到浏览器控制台执行。</li>
</ol>

<p style="margin-top:26px;color:#898781;font-size:13px">
  数据来源：<code>data/resume/facts.yaml</code>（改完重跑 <code>python scripts/build_autofill.py</code> 重新生成）
</p>
</body></html>
"""

    page_path = APPLY_DIR / "bookmarklet.html"
    page_path.write_text(page, encoding="utf-8")

    print(f"已生成 {readable.relative_to(ROOT)}（{len(js)} 字节）")
    print(f"已生成 {page_path.relative_to(ROOT)}（书签长度 {len(bookmarklet)} 字符）")
    print()
    print("  打开 bookmarklet.html，把按钮拖到书签栏即可使用。")
    empty = [k for k, v in profile.items() if not v]
    if empty:
        print(f"  未设置字段：{'、'.join(empty)}（需要的话在 facts.yaml 里补）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
