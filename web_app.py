#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
株価ダッシュボード - Flask Webサーバー
使い方: python web_app.py
ブラウザで http://localhost:5000 にアクセス
"""

import sys
import threading
from pathlib import Path

try:
    from flask import Flask, jsonify, redirect, url_for
except ImportError:
    print("Flaskが見つかりません。以下を実行してください:")
    print("  pip install flask")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
REPORT   = BASE_DIR / "report.html"

app = Flask(__name__)

_refreshing      = False
_refresh_lock    = threading.Lock()
_last_error: str = ""


# ── バックグラウンド更新 ────────────────────────────────────────────────────────

def _run_refresh() -> None:
    global _refreshing, _last_error
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True
    _last_error = ""
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "stock_app.py"), "--no-browser"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            _last_error = result.stderr[-500:] if result.stderr else "不明なエラー"
    except Exception as e:
        _last_error = str(e)
    finally:
        _refreshing = False


def start_refresh_thread() -> None:
    t = threading.Thread(target=_run_refresh, daemon=True)
    t.start()


# ── ナビバーをHTMLに注入 ────────────────────────────────────────────────────────

NAVBAR = """
<style>
  #nav-bar {
    position: fixed; top: 0; right: 0; z-index: 9999;
    display: flex; align-items: center; gap: 12px;
    padding: 8px 18px;
    background: rgba(14,14,26,.96);
    border-bottom-left-radius: 10px;
    border: 1px solid #2a2a50;
    font-family: "Segoe UI", Meiryo, sans-serif;
  }
  #nav-status { color: #8888aa; font-size: .8rem; }
  #nav-btn {
    background: #42a5f5; color: #fff; border: none;
    padding: 6px 14px; border-radius: 6px;
    font-size: .85rem; font-weight: 700; cursor: pointer;
    text-decoration: none;
  }
  #nav-btn:hover { background: #64b5f6; }
  #nav-btn:disabled { background: #444; cursor: wait; }
  .header { padding-top: 52px !important; }
</style>
<div id="nav-bar">
  <span id="nav-status"></span>
  <a id="nav-btn" href="#" onclick="doRefresh(event)">🔄 データ更新</a>
</div>
<script>
  async function doRefresh(e) {
    e.preventDefault();
    document.getElementById('nav-btn').disabled = true;
    document.getElementById('nav-status').textContent = '更新中...';
    await fetch('/api/refresh', {method:'POST'});
    pollStatus();
  }
  async function pollStatus() {
    try {
      const r = await fetch('/api/status');
      const d = await r.json();
      if (d.refreshing) {
        document.getElementById('nav-status').textContent = '取得中...';
        setTimeout(pollStatus, 2500);
      } else {
        location.reload();
      }
    } catch(_) { setTimeout(pollStatus, 3000); }
  }
  // 起動時に更新中なら自動ポーリング開始
  fetch('/api/status').then(r=>r.json()).then(d=>{
    if (d.refreshing) {
      document.getElementById('nav-status').textContent = '取得中...';
      document.getElementById('nav-btn').disabled = true;
      pollStatus();
    }
  });
</script>
"""

def inject_navbar(html: str) -> str:
    return html.replace("<body>", "<body>\n" + NAVBAR, 1)


# ── ローディング画面 ────────────────────────────────────────────────────────────

LOADING_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>データ取得中... - 株価ダッシュボード</title>
<style>
  body { background:#0e0e1a; color:#e8e8f0;
         font-family:"Segoe UI",Meiryo,sans-serif;
         display:flex; justify-content:center; align-items:center;
         height:100vh; margin:0; flex-direction:column; gap:20px; }
  .spinner { width:52px; height:52px; border:4px solid #2a2a50;
             border-top-color:#42a5f5; border-radius:50%;
             animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  p { color:#8888aa; font-size:.9rem; }
</style>
<script>
  async function poll() {
    try {
      const r = await fetch('/api/status');
      const d = await r.json();
      if (!d.refreshing) { location.href = '/'; return; }
    } catch(_) {}
    setTimeout(poll, 2500);
  }
  setTimeout(poll, 3000);
</script>
</head>
<body>
  <div class="spinner"></div>
  <h2>データ取得中...</h2>
  <p>Yahoo Finance からデータを取得しています。しばらくお待ちください。</p>
</body>
</html>"""


# ── ルーティング ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not REPORT.exists() or _refreshing:
        if not REPORT.exists():
            start_refresh_thread()
        return LOADING_HTML, 200
    html = REPORT.read_text(encoding="utf-8")
    return inject_navbar(html)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    start_refresh_thread()
    return jsonify({"started": True})


@app.route("/api/status")
def api_status():
    return jsonify({
        "refreshing": _refreshing,
        "error":      _last_error,
        "has_report": REPORT.exists(),
    })


# ── 起動 ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 50)
    print("  株価ダッシュボード Webサーバー起動")
    print("  http://localhost:5000")
    print("  終了: Ctrl+C")
    print("=" * 50)

    # 起動時にレポートがなければ自動生成
    if not REPORT.exists():
        print("[INFO] レポートが見つかりません。自動生成します...")
        start_refresh_thread()

    app.run(host="0.0.0.0", port=5000, debug=False)
