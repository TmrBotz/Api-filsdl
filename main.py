"""
FileDL Proxy Server - DEBUG VERSION
"""

import re
import json
import asyncio
import aiohttp
from aiohttp import web

FILESDL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://filmyfly.faith/",
}

CLOUD_ACTION_PRIORITY = ["cloudr2", "fastdirect", "hubcloud", "gdflix", "mediafire"]
DRIVE_BUTTON_PRIORITY = ["button2", "button", "button1", "button4"]


def get_title_size(html: str):
    title_m = re.search(r"<div[^>]+class=['\"]title['\"]>([^<]+)</div>", html)
    title = title_m.group(1).strip() if title_m else ""
    size_m = re.search(r"<div[^>]+class=['\"]info['\"]>Size:\s*([^<]+)</div>", html)
    size = size_m.group(1).strip() if size_m else ""
    return title, size


def extract_buttons_cloud(html: str) -> list[dict]:
    results = []
    pattern = re.compile(r'<button\b([^>]+)>(.*?)</button>', re.DOTALL | re.IGNORECASE)
    attr_action = re.compile(r'data-action=[\'"]([^\'"]+)[\'"]')
    attr_code   = re.compile(r'data-button-code=[\'"]([^\'"]+)[\'"]')

    for m in pattern.finditer(html):
        attrs = m.group(1)
        if "secure-download-button" not in attrs:
            continue
        a = attr_action.search(attrs)
        c = attr_code.search(attrs)
        if not a or not c:
            continue
        label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        results.append({
            "action":      a.group(1).strip(),
            "button_code": c.group(1).strip(),
            "label":       label,
        })
    return results


async def debug_post(
    session: aiohttp.ClientSession,
    page_url: str,
    file_id: str,
    action: str,
    button_code: str,
) -> dict:
    """POST karo aur raw response return karo debug ke liye."""
    post_url = f"https://new1.filesdl.in/cloud/{file_id}"
    post_data = {
        "id":          file_id,
        "action":      action,
        "button_code": button_code,
    }
    post_headers = {
        **FILESDL_HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer":       page_url,
        "Origin":        "https://new1.filesdl.in",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        async with session.post(
            post_url,
            data=post_data,
            headers=post_headers,
            allow_redirects=False,
        ) as resp:
            body = await resp.text(errors="ignore")
            return {
                "status_code": resp.status,
                "final_url":   str(resp.url),
                "location":    resp.headers.get("Location", ""),
                "content_type": resp.headers.get("Content-Type", ""),
                "response_headers": dict(resp.headers),
                "body_preview": body[:2000],  # pehle 2000 chars
            }
    except Exception as e:
        return {"error": str(e)}


async def handle_debug(request: web.Request) -> web.Response:
    target_url = request.query.get("url", "").strip()
    if not target_url or "filesdl.in" not in target_url:
        return web.json_response({"error": "valid filesdl.in url do"}, status=400)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(target_url, headers=FILESDL_HEADERS, allow_redirects=True) as resp:
            html = await resp.text()

        id_match = re.search(r"/cloud/([^/?#]+)", target_url)
        if not id_match:
            return web.json_response({"error": "cloud ID nahi mila"}, status=400)
        file_id = id_match.group(1)

        buttons = extract_buttons_cloud(html)

        results = []
        for btn in buttons:
            debug = await debug_post(session, target_url, file_id, btn["action"], btn["button_code"])
            results.append({
                "button": btn,
                "post_response": debug,
            })

        return web.json_response({
            "file_id": file_id,
            "buttons_found": buttons,
            "post_results": results,
        }, dumps=lambda x: json.dumps(x, indent=2, ensure_ascii=False))


# ── Baaki normal handlers ──────────────────────────────────────

def extract_links_drive(html: str) -> list[dict]:
    results = []
    seen_urls = set()
    pattern = re.compile(
        r"<a\s+href=['\"]([^'\"]+)['\"][^>]+class=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        re.DOTALL
    )
    for m in pattern.finditer(html):
        url       = m.group(1).strip()
        btn_class = m.group(2).strip()
        label     = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        if not url.startswith("http") or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append({"label": label, "url": url, "class": btn_class})
    if not results:
        return []
    def priority(item):
        try:
            return DRIVE_BUTTON_PRIORITY.index(item["class"])
        except ValueError:
            return len(DRIVE_BUTTON_PRIORITY)
    results.sort(key=priority)
    return [{"label": r["label"], "url": r["url"]} for r in results]


async def handle_request(request: web.Request) -> web.Response:
    target_url = request.query.get("url", "").strip()
    if not target_url:
        return web.json_response({"error": "url parameter required"}, status=400)
    if "filesdl.in" not in target_url:
        return web.json_response({"error": "Only filesdl.in URLs allowed"}, status=400)

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(target_url, headers=FILESDL_HEADERS, allow_redirects=True) as resp:
            if resp.status != 200:
                return web.json_response({"error": f"HTTP {resp.status}"}, status=resp.status)
            html = await resp.text()

        if "/drive/" in target_url:
            title_m = re.search(r"<div[^>]+class=['\"]title['\"]>([^<]+)</div>", html)
            title = title_m.group(1).strip() if title_m else ""
            size_m = re.search(r"<div[^>]+class=['\"]info['\"]>Size:\s*([^<]+)</div>", html)
            size = size_m.group(1).strip() if size_m else ""
            links = extract_links_drive(html)
            if not links:
                return web.json_response({"error": "No links found"}, status=404)
            return web.json_response({"title": title, "size": size, **links[0]})

        return web.json_response({"error": "Use /debug for cloud testing"}, status=400)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "filesdl-proxy-debug"})


app = web.Application()
app.router.add_get("/", handle_request)
app.router.add_get("/debug", handle_debug)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
