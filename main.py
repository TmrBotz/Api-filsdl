"""
FileDL Proxy Server
- Cloud links: GET redirect approach (JS se copy kiya)
- Drive links: anchor tags extract karo
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

# Priority order for cloud buttons
CLOUD_ACTION_PRIORITY = ["cloudr2", "fastdirect", "hubcloud", "gdflix", "mediafire", "gofile", "telegram"]

# Priority order for drive buttons (by class)
DRIVE_BUTTON_PRIORITY = ["button2", "button", "button1", "button4"]


def get_title_size(html: str):
    title_m = re.search(r"<div[^>]+class=['\"]title['\"]>([^<]+)</div>", html)
    title = title_m.group(1).strip() if title_m else ""
    size_m = re.search(r"<div[^>]+class=['\"]info['\"]>Size:\s*([^<]+)</div>", html)
    size = size_m.group(1).strip() if size_m else ""
    return title, size


def extract_buttons_cloud(html: str) -> list[dict]:
    """
    HTML se secure-download-button extract karo.
    Har button ka action, button_code, aur token_btn chahiye.
    """
    results = []
    pattern = re.compile(r'<button\b([^>]+)>(.*?)</button>', re.DOTALL | re.IGNORECASE)
    attr_action    = re.compile(r'data-action=[\'"]([^\'"]+)[\'"]')
    attr_code      = re.compile(r'data-button-code=[\'"]([^\'"]+)[\'"]')
    attr_token_btn = re.compile(r'data-token-btn=[\'"]([^\'"]+)[\'"]')

    for m in pattern.finditer(html):
        attrs = m.group(1)
        if "secure-download-button" not in attrs:
            continue
        a  = attr_action.search(attrs)
        c  = attr_code.search(attrs)
        tb = attr_token_btn.search(attrs)
        if not a or not c or not tb:
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
    """Priority ke hisaab se best button chuno."""
    for action in CLOUD_ACTION_PRIORITY:
        for btn in buttons:
            if btn["action"] == action:
                return btn
    return buttons[0] if buttons else None


async def resolve_cloud_url(
    session: aiohttp.ClientSession,
    page_url: str,
    file_id: str,
    action: str,
    button_code: str,
    token_btn: str,
) -> str | None:
    """
    JS ki tarah GET request karo with query params.
    Redirect follow karo aur final download URL lo.
    
    JS logic (from HTML):
        target = new URL(window.location.href)  -> page_url
        target.searchParams.set('id', file_id)
        target.searchParams.set('srv', action)
        target.searchParams.set('button_code', button_code)
        target.searchParams.set('tokenbtn', token_btn)
        window.location.assign(target)
    """
    from urllib.parse import urlparse, urlencode, urlunparse, parse_qs

    parsed = urlparse(page_url)
    # Existing query params hata do, sirf naye set karo
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

    try:
        # Pehla hop: same page pe GET with params
        async with session.get(
            redirect_url,
            headers=get_headers,
            allow_redirects=False,
        ) as resp:
            location = resp.headers.get("Location", "")
            if location:
                # Redirect mila, ab usse follow karo final URL tak
                async with session.get(
                    location,
                    headers=get_headers,
                    allow_redirects=True,
                ) as final_resp:
                    return str(final_resp.url)
            elif resp.status == 200:
                # Koi redirect nahi, shayad JSON/HTML response me URL ho
                body = await resp.text(errors="ignore")
                # JSON me url field check karo
                try:
                    data = json.loads(body)
                    return data.get("url") or data.get("link") or data.get("download_url")
                except Exception:
                    pass
                # HTML me meta refresh ya direct link check karo
                meta_m = re.search(r'<meta[^>]+http-equiv=[\'"]refresh[\'"][^>]+content=[\'"][^;]+;\s*url=([^\'"]+)[\'"]', body, re.IGNORECASE)
                if meta_m:
                    return meta_m.group(1).strip()
                # a[href] with download link
                href_m = re.search(r'href=[\'"]([^\'"]+(?:download|dl|file)[^\'"]*)[\'"]', body, re.IGNORECASE)
                if href_m:
                    return href_m.group(1).strip()
            return None
    except Exception as e:
        return None


# ── Handlers ──────────────────────────────────────────────────

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

        # Page fetch karo
        async with session.get(target_url, headers=FILESDL_HEADERS, allow_redirects=True) as resp:
            if resp.status != 200:
                return web.json_response({"error": f"HTTP {resp.status}"}, status=resp.status)
            html = await resp.text()
            final_page_url = str(resp.url)  # redirect ke baad actual URL

        title, size = get_title_size(html)

        # ── Drive link ─────────────────────────────────────────
        if "/drive/" in target_url:
            links = extract_links_drive(html)
            if not links:
                return web.json_response({"error": "No links found"}, status=404)
            return web.json_response({
                "title": title,
                "size":  size,
                **links[0],
            })

        # ── Cloud link ─────────────────────────────────────────
        if "/cloud/" in target_url:
            # file_id URL se nikalo
            id_match = re.search(r"/cloud/([^/?#]+)", target_url)
            if not id_match:
                return web.json_response({"error": "Cloud ID nahi mila URL me"}, status=400)
            file_id = id_match.group(1)

            # Buttons extract karo
            buttons = extract_buttons_cloud(html)
            if not buttons:
                return web.json_response({"error": "Koi download button nahi mila"}, status=404)

            # Best button chuno
            best = pick_best_button(buttons)
            if not best:
                return web.json_response({"error": "Button select nahi ho saka"}, status=500)

            # Download URL resolve karo
            download_url = await resolve_cloud_url(
                session,
                final_page_url,
                file_id,
                best["action"],
                best["button_code"],
                best["token_btn"],
            )

            if not download_url:
                return web.json_response({
                    "error":  "Download URL resolve nahi hua",
                    "action": best["action"],
                    "label":  best["label"],
                }, status=502)

            return web.json_response({
                "title":        title,
                "size":         size,
                "action":       best["action"],
                "label":        best["label"],
                "download_url": download_url,
            })

        return web.json_response({"error": "URL /cloud/ ya /drive/ hona chahiye"}, status=400)


# ── Debug handler (sabhi buttons test karo) ───────────────────

async def handle_debug(request: web.Request) -> web.Response:
    target_url = request.query.get("url", "").strip()
    if not target_url or "filesdl.in" not in target_url:
        return web.json_response({"error": "valid filesdl.in url do"}, status=400)

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(target_url, headers=FILESDL_HEADERS, allow_redirects=True) as resp:
            html = await resp.text()
            final_page_url = str(resp.url)

        id_match = re.search(r"/cloud/([^/?#]+)", target_url)
        if not id_match:
            return web.json_response({"error": "cloud ID nahi mila"}, status=400)
        file_id = id_match.group(1)

        buttons = extract_buttons_cloud(html)
        title, size = get_title_size(html)

        results = []
        for btn in buttons:
            url = await resolve_cloud_url(
                session,
                final_page_url,
                file_id,
                btn["action"],
                btn["button_code"],
                btn["token_btn"],
            )
            results.append({
                "button":       btn,
                "resolved_url": url,
            })

        return web.json_response({
            "file_id":      file_id,
            "title":        title,
            "size":         size,
            "buttons_found": buttons,
            "results":      results,
        }, dumps=lambda x: json.dumps(x, indent=2, ensure_ascii=False))


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "filesdl-proxy"})


# ── App setup ─────────────────────────────────────────────────

app = web.Application()
app.router.add_get("/",       handle_request)
app.router.add_get("/debug",  handle_debug)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
