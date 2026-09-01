# -*- coding: utf-8 -*-
"""ETF 估值日报生成器（云端版，GitHub Actions 运行）。

数据源：原版公开仓库 bryanzhang1024/etf-dashboard-auto 的 docs/assets.csv
        （其 CI 每工作日北京 18:30 更新；本脚本 18:45 跟进构建）。
输出：仓库根 index.html（GitHub Pages 发布）。
评分口径与 etf-dashboard-auto/docs/index.html 完全一致：
  valueScore = clamp(round((100 - max(pe_pct, pb_pct)) / 8), 0, 12)
  painScore  = clamp(round(drawdown * 16), 0, 8)
  total 0~20 → ≥18 猎杀区 / ≥14 稳中求胜 / ≥10 中性观察 / ≥6 高位警戒 / else 泡沫区

仅用标准库；PE 分位 ▼▲ 为与上一版数据的真实对比（经 commits API 取上一版）。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import urllib.request
from pathlib import Path

REPO_OUT = Path(__file__).resolve().parent.parent / "index.html"
SOURCE_REPO = "bryanzhang1024/etf-dashboard-auto"
SOURCE_CSV = f"https://raw.githubusercontent.com/{SOURCE_REPO}/main/docs/assets.csv"
COMMITS_API = f"https://api.github.com/repos/{SOURCE_REPO}/commits?path=docs/assets.csv&per_page=2"
STALE_DAYS = 4
BJT = dt.timezone(dt.timedelta(hours=8))

RATING_BUCKETS = [
    (18, "猎杀区", "🎯", "#059669", 5),
    (14, "稳中求胜", "📈", "#65a30d", 4),
    (10, "中性观察", "🔍", "#64748b", 3),
    (6, "高位警戒", "⚠️", "#d97706", 2),
    (0, "泡沫区", "💥", "#e11d48", 1),
]
EVA_NAME = {"low": "低估", "mid": "中性", "high": "高估"}
WEEKDAY_CN = "一二三四五六日"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def http_get(url: str) -> bytes:
    headers = {"User-Agent": "etf-digest-builder"}
    # 匿名 API 有 60 次/时限流；本地/Actions 有 token 时带上
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_rows(text: str) -> list[dict]:
    return [r for r in csv.DictReader(io.StringIO(text)) if r.get("index_name")]


def score(asset: dict) -> dict:
    pe_pct = float(asset["pe_pct"]) if asset.get("pe_pct") else 100.0
    pb_pct = float(asset["pb_pct"]) if asset.get("pb_pct") else 100.0
    dd = float(asset["drawdown"]) if asset.get("drawdown") else 0.0
    value = clamp(round((100 - max(pe_pct, pb_pct)) / 8), 0, 12)
    pain = clamp(round(dd * 16), 0, 8)
    total = value + pain
    label, stars = RATING_BUCKETS[-1][1], RATING_BUCKETS[-1][4]
    for lo, lab, _, _, st in RATING_BUCKETS:
        if total >= lo:
            label, stars = lab, st
            break
    eva = (asset.get("eva_type") or "").lower()
    local_dir = "low" if total >= 14 else ("high" if total < 10 else "mid")
    danjuan_dir = {"low": "low", "high": "high"}.get(eva, "mid")
    return {
        "total": total, "value": value, "pain": pain,
        "rating": label, "stars": stars,
        "eva": eva, "cross": "agree" if local_dir == danjuan_dir and eva else ("diverge" if eva else "none"),
        "pe_pct": pe_pct, "pb_pct": pb_pct, "drawdown": dd,
    }


def market_of(code: str) -> str:
    c = code.upper()
    if c[:2] in ("SH", "SZ") or c[:3] == "CSI":
        return ""
    return "港股" if c.startswith("HK") else "海外"


def fetch_source() -> tuple[list[dict], str, dict[str, float]]:
    """返回 (当前rows, 快照日期, 上一版 pe_pct 映射)。"""
    commits = json.loads(http_get(COMMITS_API).decode("utf-8"))
    if not commits:
        raise RuntimeError("源仓库 commits API 为空")
    snap_date = commits[0]["commit"]["committer"]["date"][:10]
    rows = parse_rows(http_get(SOURCE_CSV).decode("utf-8"))
    prev = {}
    if len(commits) >= 2:
        prev_sha = commits[1]["sha"]
        prev_url = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{prev_sha}/docs/assets.csv"
        try:
            for r in parse_rows(http_get(prev_url).decode("utf-8")):
                prev[r["index_code"]] = float(r["pe_pct"])
        except Exception:
            pass  # 上一版拉不到时仅省略 ▼▲
    return rows, snap_date, prev


def build_rows(assets: list[dict], prev: dict[str, float]) -> list[dict]:
    rows = []
    for a in assets:
        s = score(a)
        delta = None
        if a["index_code"] in prev:
            delta = s["pe_pct"] - prev[a["index_code"]]
        rows.append({**a, "score": s, "delta": delta})
    rows.sort(key=lambda r: -r["score"]["total"])
    return rows


def _card(r: dict, rank: int) -> str:
    import html as _h
    s = r["score"]
    a = r
    eva_cls = s["eva"] if s["eva"] in ("low", "mid", "high") else "mid"
    delta_html = ""
    if r["delta"] is not None and abs(r["delta"]) >= 0.05:
        d_cls = "down" if r["delta"] < 0 else "up"
        delta_html = f'<i class="delta {d_cls}">{"▼" if r["delta"] < 0 else "▲"}{abs(r["delta"]):.1f}</i>'
    market = market_of(a["index_code"])
    market_html = f'<span class="market">{market}·跨体制对照</span>' if market else ""
    stars = "★" * s["stars"] + "☆" * (5 - s["stars"])
    if s["cross"] == "agree":
        cross_html = f'<div class="cross ok">✓ 与蛋卷标签（{EVA_NAME[s["eva"]]}）方向互证</div>'
    elif s["cross"] == "diverge":
        cross_html = f'<div class="cross warn">⚠ 与蛋卷标签（{EVA_NAME[s["eva"]]}）方向分歧，建议人工复核</div>'
    else:
        cross_html = '<div class="cross none">蛋卷标签缺失，仅有本地公式口径</div>'
    color = next(b[3] for b in RATING_BUCKETS if b[1] == s["rating"])
    return f"""
<article class="card">
  <div class="card-head">
    <div class="card-title">
      <div class="name-row">
        <span class="rank">#{rank}</span>
        <h3>{_h.escape(a['index_name'])}</h3>
        <span class="badge {eva_cls}">{EVA_NAME.get(s['eva'], '—')}</span>{market_html}
      </div>
      <div class="code">{_h.escape(a['index_code'])} · {_h.escape(a['etfs'])}</div>
    </div>
    <div class="score-box" style="color:{color}">
      <div class="score">{s['total']}</div>
      <div class="rating-label">{s['rating']}</div>
      <div class="stars">{stars}</div>
    </div>
  </div>
  <div class="metrics">
    <div class="metric"><div class="m-label">PE / 百分位</div>
      <div class="m-value">{float(a['pe']):.2f}<span class="m-sub">· {s['pe_pct']:.1f}%</span>{delta_html}</div></div>
    <div class="metric"><div class="m-label">PB / 百分位</div>
      <div class="m-value">{float(a['pb']):.2f}<span class="m-sub">· {s['pb_pct']:.1f}%</span></div></div>
    <div class="metric"><div class="m-label">股息率</div>
      <div class="m-value em">{float(a['dividend']) * 100:.2f}%</div></div>
    <div class="metric"><div class="m-label">ROE</div>
      <div class="m-value sky">{float(a['roe']) * 100:.2f}%</div></div>
    <div class="metric"><div class="m-label">回撤（十年）</div>
      <div class="m-value sky">{s['drawdown'] * 100:.1f}%</div></div>
    <div class="metric"><div class="m-label">Value / Pain</div>
      <div class="m-value">{s['value']}<span class="m-sub">/12</span> · {s['pain']}<span class="m-sub">/8</span></div></div>
  </div>
  <div class="derive">PE分位 {s['pe_pct']:.1f}% · PB分位 {s['pb_pct']:.1f}% → 取较差 {max(s['pe_pct'], s['pb_pct']):.1f}% → 价值分 {s['value']}/12；回撤 {s['drawdown'] * 100:.1f}% → 疼痛分 {s['pain']}/8；合计 {s['total']}/20</div>
  {cross_html}
</article>"""


DIGEST_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
:root { --ink:#0f172a; --sub:#64748b; --line:#e2e8f0; --blue:#0b5fa5;
        --em:#059669; --sky:#0284c7; --rose:#e11d48; --amber:#d97706; }
body { font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
       background: #f1f5f9; color: var(--ink); }
.wrap { max-width: 640px; margin: 0 auto; padding: 14px 12px 40px; }
.hero { background: linear-gradient(135deg, #0c4a6e 0%, #0b5fa5 60%, #1d7ac4 100%);
        border-radius: 16px; padding: 18px 16px; color: #fff; box-shadow: 0 10px 26px rgba(11,95,165,.28); }
.hero h1 { font-size: 20px; letter-spacing: .5px; }
.hero-date { font-size: 12px; opacity: .85; margin-top: 4px; }
.hero-stats { display: flex; gap: 10px; margin-top: 14px; }
.hero-stats > div { flex: 1; background: rgba(255,255,255,.13); border: 1px solid rgba(255,255,255,.18);
                    border-radius: 10px; padding: 8px 6px; text-align: center; }
.hero-stats b { display: block; font-size: 22px; font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace; }
.hero-stats span { font-size: 11px; opacity: .85; }
.hero-stats .hl-em b { color: #6ee7b7; } .hero-stats .hl-rose b { color: #fda4af; }
.health { margin-top: 10px; background: rgba(255,255,255,.1); border-radius: 10px; padding: 8px 12px;
          font-size: 12px; line-height: 1.7; }
.health .lv-ok { color: #a7f3d0; } .health .lv-warn { color: #fde68a; }
.health .lv-error { color: #fecaca; } .health .lv-info { color: rgba(255,255,255,.75); }
.bucket { margin-top: 22px; }
.bucket h2 { font-size: 16px; font-weight: 700; display: flex; align-items: baseline; gap: 8px;
             padding: 2px 0 2px 10px; border-left: 4px solid currentColor; margin-bottom: 10px; }
.bucket h2 .range { font-size: 11.5px; font-weight: 500; color: var(--sub); }
.card { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 14px 14px 12px;
        margin-bottom: 10px; box-shadow: 0 2px 10px rgba(15,23,42,.05); }
.card-head { display: flex; justify-content: space-between; gap: 10px; padding-bottom: 10px;
             border-bottom: 1px dashed var(--line); }
.name-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.name-row h3 { font-size: 16px; }
.rank { font-size: 11px; font-weight: 600; color: var(--blue); background: #e8f2fb; border-radius: 5px;
        padding: 1px 5px; }
.badge { font-size: 11px; font-weight: 600; padding: 1.5px 7px; border-radius: 6px; }
.badge.low { color: #047857; background: #d1fae5; } .badge.mid { color: #b45309; background: #fef3c7; }
.badge.high { color: #be123c; background: #ffe4e6; }
.market { font-size: 10px; color: var(--sub); border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; }
.code { font-size: 11px; color: var(--sub); margin-top: 5px; letter-spacing: .3px; }
.score-box { text-align: right; flex-shrink: 0; }
.score { font-size: 30px; font-weight: 800; line-height: 1;
         font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace; }
.rating-label { font-size: 12px; font-weight: 600; margin-top: 2px; }
.stars { font-size: 12px; color: #f59e0b; letter-spacing: 1px; }
.metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; padding: 11px 0 4px; }
.metric .m-label { font-size: 10.5px; color: var(--sub); }
.metric .m-value { font-size: 15px; font-weight: 700; margin-top: 1px;
                   font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace; }
.m-value.em { color: var(--em); } .m-value.sky { color: var(--sky); }
.m-sub { font-size: 11px; color: var(--sub); font-weight: 500; }
.delta { font-style: normal; font-size: 10.5px; font-weight: 600; margin-left: 3px; }
.delta.down { color: var(--em); } .delta.up { color: var(--rose); }
.derive { font-size: 11px; color: var(--sub); line-height: 1.7; margin-top: 6px; }
.cross { font-size: 11.5px; margin-top: 5px; }
.cross.ok { color: var(--em); } .cross.warn { color: var(--amber); }
.cross.none { color: #94a3b8; }
.foot { margin-top: 26px; font-size: 11px; color: var(--sub); line-height: 1.9;
        border-top: 1px solid var(--line); padding-top: 12px; }
"""


def build_page(rows, snap_date: str) -> str:
    import html as _h
    by_rating = {b[1]: [] for b in RATING_BUCKETS}
    for r in rows:
        by_rating[r["score"]["rating"]].append(r)
    hunt_n, bubble_n = len(by_rating["猎杀区"]), len(by_rating["泡沫区"])
    diverge_n = sum(1 for r in rows if r["score"]["cross"] == "diverge")

    sections, rank = [], 0
    for _, label, emoji, color, _stars in RATING_BUCKETS:
        group = by_rating[label]
        if not group:
            continue
        hi, lo = group[0]["score"]["total"], group[-1]["score"]["total"]
        cards = []
        for r in group:
            rank += 1
            cards.append(_card(r, rank))
        sections.append(
            f'<section class="bucket"><h2 style="color:{color}">{emoji} {_h.escape(label)}'
            f'<span class="range">{hi}~{lo}分 · {len(group)}个</span></h2>{"".join(cards)}</section>'
        )

    # 数据新鲜度（云端无法查私有仓库 CI，以快照日期代替）
    now_bj = dt.datetime.now(BJT)
    try:
        age = (now_bj.date() - dt.date.fromisoformat(snap_date)).days
    except ValueError:
        age = 99
    if age >= STALE_DAYS:
        health_html = f'<div class="health"><div class="lv-error">⚠ 数据已 {age} 天未更新（源仓库 CI 可能异常），当前为 {snap_date} 快照</div></div>'
    else:
        health_html = f'<div class="health"><div class="lv-ok">✅ 数据新鲜：{snap_date} 快照（源仓库每工作日 18:30 更新）</div></div>'

    weekday = WEEKDAY_CN[now_bj.weekday()]
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF 估值日报</title>
<style>{DIGEST_CSS}</style></head>
<body><div class="wrap">
  <header class="hero">
    <h1>ETF 估值日报</h1>
    <div class="hero-date">数据快照 {_h.escape(snap_date)} 收盘 ｜ 构建于 {now_bj:%m-%d %H:%M}（周{weekday}，云端自动） ｜ PE分位▼▲为较上一交易日变化</div>
    <div class="hero-stats">
      <div><b>20</b><span>指数</span></div>
      <div class="hl-em"><b>{hunt_n}</b><span>🎯猎杀区</span></div>
      <div class="hl-rose"><b>{bubble_n}</b><span>💥泡沫区</span></div>
      <div><b>{diverge_n}</b><span>⚠口径分歧</span></div>
    </div>
    {health_html}
  </header>
  {"".join(sections)}
  <footer class="foot">
    口径与 <a href="https://github.com/bryanzhang1024/etf-dashboard-auto">etf-dashboard-auto</a> 一致：
    价值分 = clamp((100 − max(PE,PB)分位)/8, 0, 12)；疼痛分 = clamp(回撤×16, 0, 8)；总分 0~20。<br>
    分位基于十年池内滚动窗口，档位是池内排序语言，非绝对买点；深回撤≠便宜。
    港股/海外为跨体制对照，不宜与 A 股直接比价。<br>
    数据源：bryanzhang1024/etf-dashboard-auto（每工作日 18:30 自动更新）｜ 本页由
    <a href="https://github.com/whatgaohui/etf-digest/actions">GitHub Actions</a> 于 {now_bj:%m-%d %H:%M} 自动构建。
  </footer>
</div></body></html>"""


def main():
    assets, snap_date, prev = fetch_source()
    rows = build_rows(assets, prev)
    REPO_OUT.write_text(build_page(rows, snap_date), encoding="utf-8")
    print(f"index.html 已生成（快照 {snap_date}，{len(rows)} 指数）")


if __name__ == "__main__":
    main()
