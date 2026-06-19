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
    from playwright.async_api import async_playwright

    search_url = f"https://www.youtube.com/results?search_query={quote_plus(keyword)}"
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # ユーザーエージェントを一般的なブラウザに偽装
        await page.set_extra_http_headers({
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
        })

        await page.goto(search_url, wait_until="networkidle", timeout=30000)

        # 動画カードが表示されるまで待機
        await page.wait_for_selector("ytd-video-renderer", timeout=15000)

        # 上位10件を取得
        videos = await page.query_selector_all("ytd-video-renderer")
        for i, video in enumerate(videos[:10], start=1):
            try:
                title_el   = await video.query_selector("#video-title")
                channel_el = await video.query_selector("#channel-name a, #channel-name yt-formatted-string")
                meta_els   = await video.query_selector_all("#metadata-line span")

                title   = (await title_el.inner_text()).strip()   if title_el   else "N/A"
                channel = (await channel_el.inner_text()).strip() if channel_el else "N/A"

                # 動画URL
                video_url = ""
                if title_el:
                    href = await title_el.get_attribute("href")
                    if href:
                        video_url = f"https://www.youtube.com{href}" if href.startswith("/") else href

                # チャンネルURL
                channel_url = ""
                channel_a = await video.query_selector("#channel-name a")
                if channel_a:
                    href = await channel_a.get_attribute("href")
                    if href:
                        channel_url = f"https://www.youtube.com{href}" if href.startswith("/") else href

                views = "N/A"
                date  = "N/A"
                if len(meta_els) >= 2:
                    views = (await meta_els[0].inner_text()).strip()
                    date  = (await meta_els[1].inner_text()).strip()

                results.append({
                    "rank":        i,
                    "title":       title,
                    "video_url":   video_url,
                    "channel":     channel,
                    "channel_url": channel_url,
                    "views":       views,
                    "date":        date,
                })
            except Exception:
                continue

        await browser.close()

    return results


@app.get("/api/search")
async def api_search(
    keyword: str = Query(..., description="検索キーワード"),
    api_key: str = Query("", description="ラッコキーワードAPIキー"),
):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required")

    # 並列実行
    volume_task  = asyncio.create_task(fetch_rakko_volume(keyword, api_key))
    scrape_task  = asyncio.create_task(scrape_youtube(keyword))

    volume_data, scrape_data = await asyncio.gather(volume_task, scrape_task)

    return JSONResponse({
        "keyword": keyword,
        "volume":  volume_data,
        "videos":  scrape_data,
    })


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
