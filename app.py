import os
import sys
import subprocess
import platform
import asyncio
from urllib.parse import quote_plus, urlencode
from pathlib import Path

import requests
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

BASE_DIR = Path(__file__).parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = BASE_DIR / "index.html"
    return html_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------

async def fetch_rakko_volume(keyword: str, api_key: str) -> dict:
    """
    ラッコキーワードAPIで月間検索ボリュームを取得する。
    公式ドキュメント: https://related-keywords.com/n/api/doc
    """
    # --- ここを実際のAPIエンドポイントに合わせて書き換えてください ---
    # 現時点では仕様が確定していないため、モックデータを返します。
    #
    # 実装例（仮）:
    # url = "https://related-keywords.com/api/v1/volume"
    # headers = {"Authorization": f"Bearer {api_key}"}
    # params  = {"keyword": keyword, "country": "jp"}
    # resp    = requests.get(url, headers=headers, params=params, timeout=10)
    # resp.raise_for_status()
    # data = resp.json()
    # return {
    #     "keyword": keyword,
    #     "monthly_volume": data.get("volume"),
    #     "competition":    data.get("competition"),
    # }
    # -----------------------------------------------------------------

    # モックデータ（APIキーが未入力の場合も含む）
    mock_num = 12000
    return {
        "keyword": keyword,
        "monthly_volume": f"（モック）{mock_num:,}",
        "monthly_volume_num": mock_num,

    }


async def scrape_youtube(keyword: str) -> list[dict]:
    """YouTube Innertube API（内部JSON API）で検索結果を取得する。Playwrightは使わない。"""
    url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20240601.00.00",
                "hl": "ja",
                "gl": "JP",
            }
        },
        "query": keyword,
        "params": "EgIQAQ%3D%3D",  # 動画のみフィルタ
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9",
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": "2.20240601.00.00",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    results = []
    try:
        sections = (
            data["contents"]
            ["twoColumnSearchResultsRenderer"]
            ["primaryContents"]
            ["sectionListRenderer"]
            ["contents"]
        )
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                vr = item.get("videoRenderer")
                if not vr:
                    continue

                video_id = vr.get("videoId", "")
                title    = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", []))
                channel  = "".join(r.get("text", "") for r in vr.get("ownerText", {}).get("runs", []))

                ch_base = (
                    vr.get("ownerText", {})
                    .get("runs", [{}])[0]
                    .get("navigationEndpoint", {})
                    .get("browseEndpoint", {})
                    .get("canonicalBaseUrl", "")
                )
                channel_url = f"https://www.youtube.com{ch_base}" if ch_base else ""

                views = (
                    vr.get("viewCountText", {}).get("simpleText", "")
                    or "".join(r.get("text", "") for r in vr.get("viewCountText", {}).get("runs", []))
                    or "N/A"
                )
                date = vr.get("publishedTimeText", {}).get("simpleText", "N/A")

                results.append({
                    "rank":        len(results) + 1,
                    "title":       title or "N/A",
                    "video_url":   f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                    "channel":     channel or "N/A",
                    "channel_url": channel_url,
                    "views":       views,
                    "date":        date,
                })
                if len(results) >= 10:
                    break
            if len(results) >= 10:
                break
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"YouTube レスポンス解析エラー: {e}")

    return results


@app.get("/api/search")
async def api_search(
    keyword: str = Query(..., description="検索キーワード"),
    api_key: str = Query("", description="ラッコキーワードAPIキー"),
):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")

    try:
        # 並列実行
        volume_task  = asyncio.create_task(fetch_rakko_volume(keyword, api_key))
        scrape_task  = asyncio.create_task(scrape_youtube(keyword))

        volume_data, scrape_data = await asyncio.gather(volume_task, scrape_task)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"検索エラー: {str(e)}\n{traceback.format_exc()[-500:]}")

    return JSONResponse({
        "keyword": keyword,
        "volume":  volume_data,
        "videos":  scrape_data,
    })


# ---------------------------------------------------------------------------
# /api/suggest
# ---------------------------------------------------------------------------

@app.get("/api/suggest")
async def api_suggest(keyword: str = Query(..., description="検索キーワード")):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")
    url = "http://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "ds": "yt", "q": keyword}
    try:
        resp = requests.get(url, params=params, timeout=8,
                            headers={"Accept-Language": "ja-JP,ja;q=0.9"})
        resp.raise_for_status()
        data = resp.json()
        suggestions = data[1] if len(data) > 1 else []
        return JSONResponse({"keyword": keyword, "suggestions": suggestions})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"サジェスト取得エラー: {e}")


# ---------------------------------------------------------------------------
# /api/rakko_keywords
# ---------------------------------------------------------------------------

@app.get("/api/rakko_keywords")
async def api_rakko_keywords(
    keyword: str = Query(..., description="検索キーワード"),
    api_key: str = Query("", description="ラッコキーワードAPIキー"),
):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")

    # --- 実装例（仮）---
    # ラッコキーワードAPIの仕様確定後、以下を書き換えてください
    # url = "https://related-keywords.com/api/v1/keywords"
    # headers = {"Authorization": f"Bearer {api_key}"}
    # params = {"keyword": keyword, "country": "jp"}
    # resp = requests.get(url, headers=headers, params=params, timeout=10)
    # resp.raise_for_status()
    # data = resp.json()
    # return JSONResponse({"keyword": keyword, "results": data.get("keywords", [])})
    # --------------------

    # モックデータ
    mock_results = [
        {"keyword": f"{keyword} 入門",       "seo_difficulty": 45, "monthly_search": 8100},
        {"keyword": f"{keyword} 使い方",      "seo_difficulty": 38, "monthly_search": 5400},
        {"keyword": f"{keyword} おすすめ",    "seo_difficulty": 62, "monthly_search": 4400},
        {"keyword": f"{keyword} 初心者",      "seo_difficulty": 41, "monthly_search": 3600},
        {"keyword": f"{keyword} やり方",      "seo_difficulty": 33, "monthly_search": 2900},
        {"keyword": f"{keyword} 方法",        "seo_difficulty": 55, "monthly_search": 2400},
        {"keyword": f"{keyword} 無料",        "seo_difficulty": 70, "monthly_search": 1900},
        {"keyword": f"{keyword} 比較",        "seo_difficulty": 29, "monthly_search": 1300},
        {"keyword": f"{keyword} メリット",    "seo_difficulty": 22, "monthly_search":  880},
        {"keyword": f"{keyword} デメリット",  "seo_difficulty": 18, "monthly_search":  720},
    ]
    return JSONResponse({"keyword": keyword, "results": mock_results})


# ---------------------------------------------------------------------------
# /api/gemini_predict  (Gemini AI による行動予測キーワード)
# ---------------------------------------------------------------------------

@app.get("/api/gemini_predict")
async def api_gemini_predict(
    keyword: str = Query(...),
    gemini_api_key: str = Query("", description="Gemini APIキー"),
    rakko_api_key: str = Query("", description="ラッコキーワードAPIキー"),
    gemini_model: str = Query("gemini-2.5-flash", description="使用するGeminiモデル"),
):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")
    if not gemini_api_key.strip():
        raise HTTPException(status_code=400, detail="Gemini APIキーを入力してください")

    # --- Gemini でキーワード予測 ---
    allowed_models = {"gemini-2.5-flash", "gemini-2.0-flash"}
    model = gemini_model if gemini_model in allowed_models else "gemini-2.5-flash"
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_api_key}"
    prompt = f"""あなたはYouTubeユーザーの行動を分析する専門家です。
「{keyword}」を検索したユーザーが、次に検索しそうなキーワードを15個予測してください。
ユーザーの行動パターン・興味の遷移・深掘りニーズを考慮して、多様な角度から予測してください。

必ず以下のJSON形式のみで回答してください（他のテキストは一切不要）:
{{"keywords": ["キーワード1", "キーワード2", ..., "キーワード15"]}}"""

    resp = requests.post(gemini_url, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 512}
    }, timeout=15)

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Gemini APIエラー: {resp.text[:300]}")

    import json as _json, re as _re
    try:
        resp_json = resp.json()
        # finishReason が STOP 以外（MAX_TOKENS等）でも parts を取得
        candidates = resp_json.get("candidates", [])
        if not candidates:
            raise HTTPException(status_code=502, detail=f"Gemini レスポンスにcandidatesがありません: {resp.text[:200]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise HTTPException(status_code=502, detail=f"Gemini レスポンスにpartsがありません: {resp.text[:200]}")
        raw = parts[0].get("text", "").strip()
        raw = _re.sub(r"```json|```", "", raw).strip()
        # JSONブロックだけ抽出
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        raw = m.group(0) if m else raw
        predicted = _json.loads(raw).get("keywords", [])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini レスポンス解析エラー: {e} / raw={resp.text[:200]}")

    # --- ラッコキーワードAPIでボリューム取得（モック） ---
    import random, hashlib
    results = []
    for kw in predicted[:15]:
        seed = int(hashlib.md5(kw.encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)
        # 実装例:
        # vol_resp = requests.get("https://related-keywords.com/api/v1/volume",
        #     headers={"Authorization": f"Bearer {rakko_api_key}"},
        #     params={"keyword": kw, "country": "jp"}, timeout=8)
        # volume = vol_resp.json().get("volume")
        # seo   = vol_resp.json().get("seo_difficulty")
        volume = rng.randint(100, 50000)
        seo    = rng.randint(5, 95)
        results.append({"keyword": kw, "monthly_volume": volume, "seo_difficulty": seo})

    results.sort(key=lambda x: x["monthly_volume"], reverse=True)
    return JSONResponse({"keyword": keyword, "results": results})


# ---------------------------------------------------------------------------
# /api/open_browser
# ---------------------------------------------------------------------------

@app.get("/api/open_browser")
async def api_open_browser(keyword: str = Query(..., description="検索キーワード")):
    url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"

    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            chrome_paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
            chrome = next((p for p in chrome_paths if os.path.exists(p)), None)
            if chrome:
                subprocess.Popen([chrome, "--incognito", url])
            else:
                # Chrome が見つからない場合はデフォルトブラウザで開く
                subprocess.Popen(["open", url])

        elif system == "Windows":
            chrome_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
            ]
            chrome = next((p for p in chrome_paths if os.path.exists(p)), None)
            if chrome:
                subprocess.Popen([chrome, "--incognito", url])
            else:
                os.startfile(url)

        else:  # Linux
            subprocess.Popen(["google-chrome", "--incognito", url])

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ブラウザ起動に失敗しました: {e}")

    return {"status": "ok", "message": f"シークレットブラウザで '{keyword}' を開きました"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
