#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
株価チェックアプリ - Daily Stock Dashboard
使い方: python stock_app.py
"""

import csv
import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
    import yfinance as yf
    import requests
    import plotly.graph_objects as go
    import plotly.io as pio
except ImportError as e:
    print(f"必要なパッケージが見つかりません: {e}")
    print("以下のコマンドを実行してインストールしてください:")
    print("  pip install yfinance pandas plotly requests")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / "code.csv"
OUTPUT_FILE = BASE_DIR / "report.html"


# ── データ読み込み ──────────────────────────────────────────────────────────────

def get_yahoo_symbol(market: str, code: str) -> str:
    """市場コードをYahoo Financeシンボルに変換"""
    if market == "日本":
        return f"{code}.T"
    return str(code)


def load_portfolio() -> list:
    """CSVからポートフォリオを読み込む"""
    portfolio = []
    with open(CSV_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row.get("株式コード", "").strip()
            if not code:
                continue
            portfolio.append({
                "market": row["市場"].strip(),
                "code": code,
                "symbol": get_yahoo_symbol(row["市場"].strip(), code),
                "shares": int(row["保有数"]),
                "acquisition_price": float(row["取得金額"]),
                "target_price": float(row["目標金額"]),
            })
    return portfolio


# ── 株価データ取得 ──────────────────────────────────────────────────────────────

def fetch_stock_data(symbol: str) -> dict:
    """3か月の株価履歴と現在情報を取得"""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")
        if hist.empty:
            print(f"  [警告] {symbol}: データが取得できませんでした")
            return {"error": "データなし", "history": pd.DataFrame()}

        info = ticker.fast_info
        current_price = float(hist["Close"].iloc[-1])
        prev_price = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current_price
        day_change = current_price - prev_price
        day_change_pct = (day_change / prev_price * 100) if prev_price else 0.0

        # 会社名（infoから取得、失敗時はシンボルを使用）
        try:
            full_info = ticker.info
            name = full_info.get("shortName") or full_info.get("longName") or symbol
        except Exception:
            name = symbol

        return {
            "history": hist,
            "current_price": current_price,
            "prev_price": prev_price,
            "day_change": day_change,
            "day_change_pct": day_change_pct,
            "name": name,
        }
    except Exception as e:
        print(f"  [エラー] {symbol}: {e}")
        return {"error": str(e), "history": pd.DataFrame()}


# ── 市場ランキング取得 ──────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

def _fetch_screener(scr_id: str, count: int, region: str = "", lang: str = "") -> list:
    base = (
        f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        f"?scrIds={scr_id}&count={count}&formatted=false"
    )
    if region:
        base += f"&region={region}&lang={lang}"
    try:
        resp = requests.get(base, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return (
            data.get("finance", {})
                .get("result", [{}])[0]
                .get("quotes", [])[:count]
        )
    except Exception as e:
        print(f"  [警告] ランキング取得失敗 ({scr_id}, region={region}): {e}")
        return []


_jp_name_cache: dict = {}

def get_jp_name(symbol: str) -> str:
    """Yahoo Finance検索APIから日本語会社名を取得"""
    if symbol in _jp_name_cache:
        return _jp_name_cache[symbol]
    code = symbol.replace(".T", "").replace(".OS", "")
    try:
        url = (
            "https://query1.finance.yahoo.com/v1/finance/search"
            f"?q={code}&lang=ja&region=JP&quotesCount=10&newsCount=0"
        )
        resp = requests.get(url, headers=HEADERS, timeout=8)
        for q in resp.json().get("quotes", []):
            if q.get("symbol") == symbol:
                name = q.get("longname") or q.get("shortname") or ""
                _jp_name_cache[symbol] = name
                return name
    except Exception:
        pass
    _jp_name_cache[symbol] = ""
    return ""


def get_rankings_us(count: int = 5) -> tuple:
    gainers = _fetch_screener("day_gainers", count)
    losers  = _fetch_screener("day_losers",  count)
    return gainers, losers


def get_rankings_jp(count: int = 5) -> tuple:
    """yfinanceスクリーナーで日本株の上昇・下落ランキングを取得"""
    try:
        from yfinance import screen, EquityQuery
        jp_q = EquityQuery("is-in", ["exchange", "JPX", "FKA", "OSA", "SAP"])
        gain_raw = screen(jp_q, sortField="percentchange", sortAsc=False, size=count)
        loss_raw = screen(jp_q, sortField="percentchange", sortAsc=True,  size=count)

        def to_quote(q: dict) -> dict:
            return {
                "symbol":                      q.get("symbol", ""),
                "shortName":                   q.get("shortName") or q.get("displayName") or "",
                "regularMarketPrice":          q.get("regularMarketPrice", 0),
                "regularMarketChangePercent":  q.get("regularMarketChangePercent", 0),
            }

        gainers = [to_quote(q) for q in gain_raw.get("quotes", [])]
        losers  = [to_quote(q) for q in loss_raw.get("quotes", [])]
        return gainers, losers
    except Exception as e:
        print(f"  [警告] 日本株ランキング取得失敗: {e}")
        return [], []


# ── チャート生成 ────────────────────────────────────────────────────────────────

def create_chart(stock: dict, hist: pd.DataFrame) -> str:
    """Plotlyキャンドルスティックチャートを生成（HTML断片を返す）"""
    symbol = stock["symbol"]
    acq    = stock["acquisition_price"]
    target = stock["target_price"]
    danger = acq * (2 / 3)

    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=hist.index,
        open=hist["Open"],
        high=hist["High"],
        low=hist["Low"],
        close=hist["Close"],
        name=symbol,
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
    ))

    # 参照ライン
    fig.add_hline(
        y=target, line_dash="dash", line_color="#00e676", line_width=2,
        annotation_text=f"目標 {target:,.0f}",
        annotation_font=dict(color="#00e676", size=11),
        annotation_position="top left",
    )
    fig.add_hline(
        y=acq, line_dash="dot", line_color="#ffa726", line_width=2,
        annotation_text=f"取得 {acq:,.0f}",
        annotation_font=dict(color="#ffa726", size=11),
        annotation_position="top right",
    )
    fig.add_hline(
        y=danger, line_dash="dash", line_color="#ef5350", line_width=2,
        annotation_text=f"警戒 {danger:,.0f}",
        annotation_font=dict(color="#ef5350", size=11),
        annotation_position="bottom left",
    )

    fig.update_layout(
        height=380,
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        paper_bgcolor="rgba(18,18,30,0)",
        plot_bgcolor="rgba(18,18,30,0)",
        margin=dict(l=60, r=80, t=30, b=30),
        xaxis=dict(gridcolor="rgba(255,255,255,0.08)", showgrid=True),
        yaxis=dict(gridcolor="rgba(255,255,255,0.08)", showgrid=True),
        showlegend=False,
    )

    return pio.to_html(fig, include_plotlyjs=False, full_html=False)


# ── HTMLレポート生成 ────────────────────────────────────────────────────────────

def fmt_currency(market: str, value: float) -> str:
    if market == "日本":
        return f"¥{value:,.0f}"
    return f"${value:,.2f}"


def ranking_rows_html(items: list, is_gainer: bool, market: str) -> str:
    if not items:
        return '<tr><td colspan="5" style="text-align:center;color:#888;">データなし</td></tr>'
    rows = []
    for i, q in enumerate(items, 1):
        sym     = q.get("symbol", "")
        en_name = q.get("shortName") or q.get("displayName") or ""
        price   = q.get("regularMarketPrice", 0)
        chg     = q.get("regularMarketChangePercent", 0)
        cls     = "positive" if is_gainer else "negative"
        sign    = "+" if chg >= 0 else ""
        cur     = "¥" if market == "JP" else "$"

        if market == "JP":
            jp_name = get_jp_name(sym)
            if jp_name:
                name_cell = (
                    f'<div>{jp_name}</div>'
                    f'<div style="color:var(--muted);font-size:.78em">{en_name}</div>'
                )
            else:
                name_cell = en_name or sym
        else:
            name_cell = en_name or sym

        medal = "1位" if i == 1 else "2位" if i == 2 else "3位" if i == 3 else str(i)
        rows.append(
            f"<tr>"
            f'<td class="rank">{medal}</td>'
            f"<td><strong>{sym}</strong></td>"
            f"<td>{name_cell}</td>"
            f'<td class="number">{cur}{price:,.2f}</td>'
            f'<td class="number {cls}">{sign}{chg:.2f}%</td>'
            f"</tr>"
        )
    return "\n".join(rows)


def generate_html(portfolio_data: list,
                  jp_gainers: list, jp_losers: list,
                  us_gainers: list, us_losers: list) -> str:

    now = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")

    # ── アラートバナー ──
    alerts_html = ""
    for s in portfolio_data:
        if s.get("alert_sell"):
            alerts_html += f"""
            <div class="alert-banner alert-sell">
              🔔 <strong>売りシグナル！</strong>
              {s['name']} ({s['symbol']}) ─
              現在価格 {fmt_currency(s['market'], s['current_price'])} が
              目標価格 {fmt_currency(s['market'], s['target_price'])} に到達しました
            </div>"""
        if s.get("alert_danger"):
            alerts_html += f"""
            <div class="alert-banner alert-danger">
              🚨 <strong>大変！損失警戒ライン到達！</strong>
              {s['name']} ({s['symbol']}) ─
              現在価格 {fmt_currency(s['market'], s['current_price'])} が
              警戒ライン {fmt_currency(s['market'], s['danger_price'])} 以下です
              （取得金額の2/3）
            </div>"""
    if not alerts_html:
        alerts_html = '<div class="no-alert">✅ 現在アラートはありません</div>'

    # ── ポートフォリオ表 ──
    port_rows = ""
    for s in portfolio_data:
        chg_cls  = "positive" if s.get("day_change_pct", 0) >= 0 else "negative"
        prof_cls = "positive" if s.get("profit", 0) >= 0 else "negative"
        sign     = "+" if s.get("day_change_pct", 0) >= 0 else ""
        psign    = "+" if s.get("profit", 0) >= 0 else ""
        badges   = ""
        if s.get("alert_sell"):
            badges += '<span class="badge badge-sell">売り</span>'
        if s.get("alert_danger"):
            badges += '<span class="badge badge-danger">警戒</span>'
        port_rows += f"""
        <tr>
          <td>{s['market']}</td>
          <td><strong>{s['code']}</strong> {badges}</td>
          <td>{s.get('name', s['symbol'])}</td>
          <td class="number">{fmt_currency(s['market'], s.get('current_price', 0))}</td>
          <td class="number {chg_cls}">{sign}{s.get('day_change_pct', 0):.2f}%</td>
          <td class="number">{fmt_currency(s['market'], s['acquisition_price'])}</td>
          <td class="number">{fmt_currency(s['market'], s['target_price'])}</td>
          <td class="number">{s['shares']:,}</td>
          <td class="number {prof_cls}">{fmt_currency(s['market'], s.get('profit', 0))}</td>
          <td class="number {prof_cls}">{psign}{s.get('profit_pct', 0):.1f}%</td>
        </tr>"""

    # ── チャートセクション ──
    charts_html = ""
    for s in portfolio_data:
        if not s.get("chart"):
            continue
        alert_badges = ""
        if s.get("alert_sell"):
            alert_badges += '<span class="badge badge-sell">売りシグナル</span>'
        if s.get("alert_danger"):
            alert_badges += '<span class="badge badge-danger">大変！警戒</span>'
        charts_html += f"""
        <div class="chart-card">
          <div class="chart-title">
            <span>{s.get('name', s['symbol'])}</span>
            <span class="chart-sym">{s['symbol']}</span>
            {alert_badges}
          </div>
          {s['chart']}
        </div>"""

    jp_g_rows = ranking_rows_html(jp_gainers, True,  "JP")
    jp_l_rows = ranking_rows_html(jp_losers,  False, "JP")
    us_g_rows = ranking_rows_html(us_gainers, True,  "US")
    us_l_rows = ranking_rows_html(us_losers,  False, "US")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>株価ダッシュボード</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  :root {{
    --bg:      #0e0e1a;
    --bg2:     #16162a;
    --bg3:     #1e1e35;
    --border:  #2a2a50;
    --text:    #e8e8f0;
    --muted:   #8888aa;
    --green:   #26a69a;
    --red:     #ef5350;
    --orange:  #ffa726;
    --blue:    #42a5f5;
    --gold:    #ffd54f;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: "Segoe UI", "Hiragino Kaku Gothic Pro", Meiryo, sans-serif;
    font-size: 14px; line-height: 1.6;
  }}
  a {{ color: var(--blue); }}

  /* ─── ヘッダー ─── */
  .header {{
    background: linear-gradient(135deg, #1a1a3a 0%, #0e1a2e 100%);
    border-bottom: 1px solid var(--border);
    padding: 20px 32px;
    display: flex; justify-content: space-between; align-items: center;
  }}
  .header h1 {{ font-size: 1.5rem; color: var(--gold); letter-spacing: .05em; }}
  .header .timestamp {{ color: var(--muted); font-size: .85rem; }}

  /* ─── レイアウト ─── */
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 20px; }}
  .section-title {{
    font-size: 1.1rem; font-weight: 700; color: var(--gold);
    border-left: 3px solid var(--gold); padding-left: 10px;
    margin: 28px 0 14px;
  }}

  /* ─── アラート ─── */
  .alert-banner {{
    border-radius: 8px; padding: 12px 18px;
    margin: 6px 0; font-size: .95rem; font-weight: 500;
  }}
  .alert-sell  {{ background: rgba(38,166,154,.18); border: 1px solid #26a69a; }}
  .alert-danger {{ background: rgba(239,83,80,.18);  border: 1px solid #ef5350; }}
  .no-alert {{
    background: rgba(38,166,154,.10); border: 1px solid rgba(38,166,154,.3);
    border-radius: 8px; padding: 10px 18px; color: var(--green);
  }}

  /* ─── ポートフォリオ表 ─── */
  .table-wrap {{ overflow-x: auto; }}
  table {{
    width: 100%; border-collapse: collapse;
    background: var(--bg2); border-radius: 10px; overflow: hidden;
  }}
  thead th {{
    background: var(--bg3); padding: 10px 12px;
    text-align: left; color: var(--muted); font-weight: 600;
    font-size: .82rem; letter-spacing: .04em; white-space: nowrap;
  }}
  tbody tr:hover {{ background: rgba(255,255,255,.04); }}
  tbody td {{
    padding: 9px 12px; border-top: 1px solid var(--border); white-space: nowrap;
  }}
  .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .positive {{ color: #26c6a2; }}
  .negative {{ color: #ef5350; }}

  /* ─── バッジ ─── */
  .badge {{
    display: inline-block; border-radius: 4px;
    padding: 1px 6px; font-size: .72rem; font-weight: 700;
    margin-left: 4px; vertical-align: middle;
  }}
  .badge-sell   {{ background: rgba(38,166,154,.25); color: #26a69a; border: 1px solid #26a69a; }}
  .badge-danger {{ background: rgba(239,83,80,.25);  color: #ef5350; border: 1px solid #ef5350; }}

  /* ─── チャートグリッド ─── */
  .charts-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(560px, 1fr));
    gap: 16px;
  }}
  .chart-card {{
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden;
  }}
  .chart-title {{
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 12px 16px; background: var(--bg3);
    font-weight: 600; font-size: .95rem;
  }}
  .chart-sym {{ color: var(--muted); font-size: .82rem; }}

  /* ─── ランキング ─── */
  .ranking-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
    gap: 16px;
  }}
  .ranking-card {{
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden;
  }}
  .ranking-header {{
    padding: 10px 16px; font-weight: 700; font-size: .9rem;
    display: flex; align-items: center; gap: 8px;
  }}
  .rank-up   {{ background: rgba(38,166,154,.15); color: #26a69a; }}
  .rank-down {{ background: rgba(239,83,80,.15);  color: #ef5350; }}
  .rank {{ text-align: center; font-size: 1.1rem; }}
  .ranking-card table {{ border-radius: 0; }}
  .ranking-card thead th {{ font-size: .78rem; }}

  /* ─── フッター ─── */
  .footer {{
    text-align: center; color: var(--muted); font-size: .78rem;
    padding: 24px; margin-top: 16px;
    border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>

<div class="header">
  <h1>📈 株価ダッシュボード</h1>
  <div class="timestamp">最終更新: {now}</div>
</div>

<div class="container">

  <!-- アラート -->
  <div class="section-title">🔔 アラート</div>
  {alerts_html}

  <!-- ポートフォリオ -->
  <div class="section-title">💼 保有銘柄</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>市場</th><th>コード</th><th>銘柄名</th>
          <th>現在値</th><th>前日比</th>
          <th>取得単価</th><th>目標単価</th>
          <th>保有数</th><th>損益(合計)</th><th>損益率</th>
        </tr>
      </thead>
      <tbody>{port_rows}</tbody>
    </table>
  </div>

  <!-- チャート -->
  <div class="section-title">📊 6か月チャート（緑:目標 / 橙:取得 / 赤:警戒ライン）</div>
  <div class="charts-grid">
    {charts_html}
  </div>

  <!-- ランキング -->
  <div class="section-title">🏆 前日 値動きランキング</div>
  <div class="ranking-grid">

    <!-- 日本株 上昇 -->
    <div class="ranking-card">
      <div class="ranking-header rank-up">📈 日本株 値上がり Top5</div>
      <table>
        <thead><tr><th>#</th><th>コード</th><th>銘柄</th><th>株価</th><th>変動率</th></tr></thead>
        <tbody>{jp_g_rows}</tbody>
      </table>
    </div>

    <!-- 日本株 下落 -->
    <div class="ranking-card">
      <div class="ranking-header rank-down">📉 日本株 値下がり Top5</div>
      <table>
        <thead><tr><th>#</th><th>コード</th><th>銘柄</th><th>株価</th><th>変動率</th></tr></thead>
        <tbody>{jp_l_rows}</tbody>
      </table>
    </div>

    <!-- 米国株 上昇 -->
    <div class="ranking-card">
      <div class="ranking-header rank-up">📈 米国株 値上がり Top5</div>
      <table>
        <thead><tr><th>#</th><th>コード</th><th>銘柄</th><th>株価</th><th>変動率</th></tr></thead>
        <tbody>{us_g_rows}</tbody>
      </table>
    </div>

    <!-- 米国株 下落 -->
    <div class="ranking-card">
      <div class="ranking-header rank-down">📉 米国株 値下がり Top5</div>
      <table>
        <thead><tr><th>#</th><th>コード</th><th>銘柄</th><th>株価</th><th>変動率</th></tr></thead>
        <tbody>{us_l_rows}</tbody>
      </table>
    </div>

  </div>

</div>

<div class="footer">
  データ提供: Yahoo Finance ／ 本アプリの情報は投資助言ではありません
</div>
</body>
</html>"""


# ── メイン処理 ──────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 56)
    print("  株価ダッシュボード 起動")
    print("=" * 56)

    # 1. ポートフォリオ読み込み
    portfolio = load_portfolio()
    print(f"\n[OK] {len(portfolio)} 銘柄を読み込みました\n")

    # 2. 各銘柄のデータ取得
    portfolio_data = []
    for stock in portfolio:
        sym = stock["symbol"]
        print(f"  取得中: {sym} ({stock['market']} {stock['code']})")
        data = fetch_stock_data(sym)

        if "error" in data:
            record = {**stock, "name": sym, "current_price": 0,
                      "day_change": 0, "day_change_pct": 0,
                      "profit": 0, "profit_pct": 0,
                      "danger_price": stock["acquisition_price"] * (2/3),
                      "alert_sell": False, "alert_danger": False, "chart": ""}
        else:
            cur   = data["current_price"]
            acq   = stock["acquisition_price"]
            tgt   = stock["target_price"]
            shrs  = stock["shares"]
            danger_price = acq * (2 / 3)
            profit       = (cur - acq) * shrs
            profit_pct   = (cur - acq) / acq * 100 if acq else 0

            chart_html = create_chart(stock, data["history"])

            record = {
                **stock,
                "name":           data["name"],
                "current_price":  cur,
                "day_change":     data["day_change"],
                "day_change_pct": data["day_change_pct"],
                "profit":         profit,
                "profit_pct":     profit_pct,
                "danger_price":   danger_price,
                "alert_sell":     cur >= tgt,
                "alert_danger":   cur <= danger_price,
                "chart":          chart_html,
            }

        portfolio_data.append(record)

    # 3. 市場ランキング取得
    print("\n[INFO] 市場ランキングを取得中...")
    print("  日本株ランキング取得中...")
    jp_gainers, jp_losers = get_rankings_jp(5)
    print("  米国株ランキング取得中...")
    us_gainers, us_losers = get_rankings_us(5)

    # 4. アラートサマリー
    sell_alerts   = [s for s in portfolio_data if s.get("alert_sell")]
    danger_alerts = [s for s in portfolio_data if s.get("alert_danger")]
    if sell_alerts:
        print(f"\n[SELL] 売りシグナル: {', '.join(s['code'] for s in sell_alerts)}")
    if danger_alerts:
        print(f"[DANGER] 大変アラート: {', '.join(s['code'] for s in danger_alerts)}")

    # 5. HTML生成・保存・ブラウザで開く
    print(f"\n[INFO] レポートを生成中: {OUTPUT_FILE}")
    html = generate_html(portfolio_data, jp_gainers, jp_losers, us_gainers, us_losers)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print("[OK] 完了! ブラウザで開きます...")
    webbrowser.open(OUTPUT_FILE.as_uri())
    print("=" * 56)


if __name__ == "__main__":
    main()
