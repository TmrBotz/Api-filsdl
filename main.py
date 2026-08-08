"""
FileDL Proxy Server
- Cloud links: GET redirect approach (JS se copy kiya)
- Drive links: anchor tags extract karo
- Cloudflare bypass: cloudscraper use karo
"""

import re
import json
import asyncio
import cloudscraper
from aiohttp import web
from concurrent.futures import ThreadPoolExecutor
import os

# ── Constants ─────────────────────────────────────────────────

FILESDL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# Priority order for cloud buttons
CLOUD_ACTION_PRIORITY = [
    "cloudr2", "fastdirect", "hubcloud", "gdflix",
    "mediafire", "gofile", "telegram"
]

# Priority order for drive buttons (by class)
DRIVE_BUTTON_PRIORITY = ["button2", "button", "button1", "button4"]

# Thread pool for blocking cloudscraper calls
executor = ThreadPoolExecutor(max_workers=10)


# ── Scraper factory ───────────────────────────────────────────

def make_scraper() -> cloudscraper.CloudScraper:
    """
    Ek fresh cloudscraper instance banao.
    browser dict se actual Chrome fingerprint mimic hota hai.
    """
    return cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )


# ── Sync helpers (thread pool me run honge) ───────────────────

def sync_fetch_page(url: str) -> tuple[str, str]:
    """
    Page fetch karo, (html, final_url) return karo.
    Raises on non-200.
    """
    scraper = make_scraper()
    resp = scraper.get(url, headers=FILESDL_HEADERS, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp.text, resp.url


def sync_resolve_cloud_url(
    page_url: str,
    file_id: str,
    action: str,
    button_code: str,
    token_btn: str,
) -> str | None:
    """
    JS ki tarah GET request karo with query params.
    Cookies same scraper instance se persist hongi.
    """
    from urllib.parse import urlparse, urlencode, urlunparse

    scraper = make_scraper()

    # Pehle page fetch karo taaki cookies set ho jayein
    scraper.get(page_url, headers=FILESDL_HEADERS, timeout=30)

    parsed = urlparse(page_url)
    params = {
        "id":          file_id,
        "srv":         action,
        "button_code": button_code,
        "tokenbtn":    token_btn,
    }
    redirect_url = urlunparse(parsed._replace(query=urlencode(params)))

    get_headers = {
        **FILESDL_HEADERS,
        "Referer": page_url,
    }

    # allow_redirects=False taaki pehla Location header milے
    resp = scraper.get(redirect_url, headers=get_headers, timeout=30, allow_redirects=False)

    location = resp.headers.get("Location", "")
    if location:
        # Final redirect follow karo
        final = scraper.get(location, headers=get_headers, timeout=30, allow_redirects=True)
        return final.url

    if resp.status_code == 200:
        body = resp.text
        # JSON me url check karo
        try:
            data = json.loads(body)
            return (
                data.get("url")
                or data.get("link")
                or data.get("download_url")
            )
        except Exception:
            pass
        # Meta refresh check
        meta_m = re.search(
            r'<meta[^>]+http-equiv=[\'"]refresh[\'"][^>]+'
            r'content=[\'"][^;]+;\s*url=([^\'"]+)[\'"]',
            body, re.IGNORECASE
        )
        if meta_m:
            return meta_m.group(1).strip()
        # Direct download href
        href_m = re.search(
            r'href=[\'"]([^\'"]+(?:download|dl|file)[^\'"]*)[\'"]',
            body, re.IGNORECASE
        )
        if href_m:
            return href_m.group(1).strip()

    return None


# ── Pure parse helpers (no IO) ────────────────────────────────

def get_title_size(html: str) -> tuple[str, str]:
    title_m = re.search(r"<div[^>]+class=['\"]title['\"]>([^<]+)</div>", html)
    title   = title_m.group(1).strip() if title_m else ""
    size_m  = re.search(r"<div[^>]+class=['\"]info['\"]>Size:\s*([^<]+)</div>", html)
    size    = size_m.group(1).strip() if size_m else ""
    return title, size


def extract_buttons_cloud(html: str) -> list[dict]:
    results     = []
    pattern     = re.compile(r'<button\b([^>]+)>(.*?)</button>', re.DOTALL | re.IGNORECASE)
    attr_action = re.compile(r'data-action=[\'"]([^\'"]+)[\'"]')
    attr_code   = re.compile(r'data-button-code=[\'"]([^\'"]+)[\'"]')
    attr_token  = re.compile(r'data-token-btn=[\'"]([^\'"]+)[\'"]')

    for m in pattern.finditer(html):
        attrs = m.group(1)
        if "secure-download-button" not in attrs:
            continue
        a  = attr_action.search(attrs)
        c  = attr_code.search(attrs)
        tb = attr_token.search(attrs)
        if not (a and c and tb):
            continue
        label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        results.append({
            "action":      a.group(1).strip(),
            "button_code": c.group(1).strip(),
            "token_btn":   tb.group(1).strip(),
            "label":       label,
        })
    return results


def pick_best_button(buttons: list[dict]) -> dict | None:
    for action in CLOUD_ACTION_PRIORITY:
        for btn in buttons:
            if btn["action"] == action:
                return btn
    return buttons[0] if buttons else None


def extract_links_drive(html: str) -> list[dict]:
    results  = []
    seen     = set()
    pattern  = re.compile(
        r"<a\s+href=['\"]([^'\"]+)['\"][^>]+class=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        re.DOTALL
    )
    for m in pattern.finditer(html):
        url       = m.group(1).strip()
        btn_class = m.group(2).strip()
        label     = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
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


# ── Route handlers ────────────────────────────────────────────

async def handle_request(request: web.Request) -> web.Response:
    target_url = request.query.get("url", "").strip()
    if not target_url:
        return web.json_response({"error": "url parameter required"}, status=400)
    if "filesdl.in" not in target_url:
        return web.json_response({"error": "Only filesdl.in URLs allowed"}, status=400)

    loop = asyncio.get_event_loop()

    # ── Page fetch ────────────────────────────────────────────
    try:
        html, final_page_url = await loop.run_in_executor(
            executor, sync_fetch_page, target_url
        )
    except Exception as e:
        status = 502
        msg    = str(e)
        # requests HTTPError se status code nikalo
        if hasattr(e, "response") and e.response is not None:
            status = e.response.status_code
            msg    = f"HTTP {status}"
        return web.json_response({"error": msg}, status=status)

    title, size = get_title_size(html)

    # ── Drive link ────────────────────────────────────────────
    if "/drive/" in target_url:
        links = extract_links_drive(html)
        if not links:
            return web.json_response({"error": "No links found"}, status=404)
        return web.json_response({
            "title": title,
            "size":  size,
            **links[0],
        })

    # ── Cloud link ────────────────────────────────────────────
    if "/cloud/" in target_url:
        id_match = re.search(r"/cloud/([^/?#]+)", target_url)
        if not id_match:
            return web.json_response({"error": "Cloud ID nahi mila URL me"}, status=400)
        file_id = id_match.group(1)

        buttons = extract_buttons_cloud(html)
        if not buttons:
            return web.json_response({"error": "Koi download button nahi mila"}, status=404)

        best = pick_best_button(buttons)
        if not best:
            return web.json_response({"error": "Button select nahi ho saka"}, status=500)

        try:
            download_url = await loop.run_in_executor(
                executor,
                sync_resolve_cloud_url,
                final_page_url,
                file_id,
                best["action"],
                best["button_code"],
                best["token_btn"],
            )
        except Exception as e:
            return web.json_response({"error": f"Resolve failed: {e}"}, status=502)

        if not download_url:
            return web.json_response({"error": "Download URL resolve nahi hua"}, status=502)

        return web.json_response({
            "title": title,
            "size":  size,
            "label": best["label"],
            "url":   download_url,
        })

    return web.json_response({"error": "URL /cloud/ ya /drive/ hona chahiye"}, status=400)


async def handle_debug(request: web.Request) -> web.Response:
    """Sabhi buttons test karo — debugging ke liye."""
    target_url = request.query.get("url", "").strip()
    if not target_url or "filesdl.in" not in target_url:
        return web.json_response({"error": "valid filesdl.in url do"}, status=400)

    loop = asyncio.get_event_loop()

    try:
        html, final_page_url = await loop.run_in_executor(
            executor, sync_fetch_page, target_url
        )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)

    id_match = re.search(r"/cloud/([^/?#]+)", target_url)
    if not id_match:
        return web.json_response({"error": "cloud ID nahi mila"}, status=400)
    file_id = id_match.group(1)

    buttons      = extract_buttons_cloud(html)
    title, size  = get_title_size(html)

    async def resolve_one(btn):
        try:
            url = await loop.run_in_executor(
                executor,
                sync_resolve_cloud_url,
                final_page_url,
                file_id,
                btn["action"],
                btn["button_code"],
                btn["token_btn"],
            )
        except Exception as e:
            url = f"ERROR: {e}"
        return {"button": btn, "resolved_url": url}

    results = await asyncio.gather(*[resolve_one(b) for b in buttons])

    return web.json_response(
        {
            "file_id":       file_id,
            "title":         title,
            "size":          size,
            "buttons_found": buttons,
            "results":       list(results),
        },
        dumps=lambda x: json.dumps(x, indent=2, ensure_ascii=False),
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "filesdl-proxy"})


# ── App setup ─────────────────────────────────────────────────

app = web.Application()
app.router.add_get("/",       handle_request)
app.router.add_get("/debug",  handle_debug)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
