"""
FileDL Proxy Server
GET /?url=https://new1.filesdl.in/cloud/ID   → cloud page (POST-based)
GET /?url=https://new1.filesdl.in/drive/ID   → drive page (anchor-based)
First download link return karta hai JSON mein.
"""

import re
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

# Priority order for cloud button actions (best first)
CLOUD_ACTION_PRIORITY = ["cloudr2", "fastdirect", "hubcloud", "gdflix", "mediafire"]

# Priority order for drive button classes (best first)
DRIVE_BUTTON_PRIORITY = ["button2", "button", "button1", "button4"]


# ─────────────────────────── HELPERS ────────────────────────────

def get_title_size(html: str):
    title_m = re.search(r"<div[^>]+class=['\"]title['\"]>([^<]+)</div>", html)
    title = title_m.group(1).strip() if title_m else ""

    size_m = re.search(r"<div[^>]+class=['\"]info['\"]>Size:\s*([^<]+)</div>", html)
    size = size_m.group(1).strip() if size_m else ""

    return title, size


# ─────────────────────────── CLOUD ──────────────────────────────

def extract_buttons_cloud(html: str) -> list[dict]:
    """
    Parse <button class='... secure-download-button'
                  data-action='...'
                  data-button-code='...'>Label</button>
    """
    results = []
    pattern = re.compile(
        r'<button[^>]+class=[\'"][^\'"]*secure-download-button[^\'"]*[\'"][^>]*'
        r'data-action=[\'"]([^\'"]+)[\'"][^>]*'
        r'data-button-code=[\'"]([^\'"]+)[\'"][^>]*>'
        r'(.*?)</button>',
        re.DOTALL
    )
    for m in pattern.finditer(html):
        action      = m.group(1).strip()
        button_code = m.group(2).strip()
        label       = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        results.append({"action": action, "button_code": button_code, "label": label})
    return results


async def resolve_cloud_link(session: aiohttp.ClientSession,
                              page_url: str,
                              file_id: str,
                              action: str,
                              button_code: str) -> str | None:
    """
    POST karo cloud page pe aur redirect URL lo.
    filesdl server 302 redirect deta hai → final URL wahi hai.
    """
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
    }

    try:
        async with session.post(
            post_url,
            data=post_data,
            headers=post_headers,
            allow_redirects=True,
            max_redirects=10,
        ) as resp:
            final_url = str(resp.url)
            # Agar redirect hua aur URL alag hai toh wahi download link hai
            if final_url != post_url and final_url.startswith("http"):
                return final_url
            # Kuch servers JSON mein URL dete hain
            ct = resp.headers.get("Content-Type", "")
            if "json" in ct:
                data = await resp.json(content_type=None)
                return data.get("url") or data.get("link") or data.get("download_url")
    except Exception:
        pass
    return None


async def fetch_links_cloud(session: aiohttp.ClientSession,
                             target_url: str,
                             html: str) -> dict:
    # File ID extract karo URL se
    id_match = re.search(r"/cloud/([^/?#]+)", target_url)
    if not id_match:
        return {"error": "Cloud ID URL se nahi mila", "status": 400}
    file_id = id_match.group(1)

    title, size = get_title_size(html)

    buttons = extract_buttons_cloud(html)
    if not buttons:
        return {"error": "No download buttons found on cloud page", "status": 404}

    # Priority ke hisaab se sort karo
    def priority(btn):
        try:
            return CLOUD_ACTION_PRIORITY.index(btn["action"])
        except ValueError:
            return len(CLOUD_ACTION_PRIORITY)

    buttons.sort(key=priority)

    # Best button se shuru karo, fail hone par next try karo
    for btn in buttons:
        url = await resolve_cloud_link(
            session, target_url, file_id, btn["action"], btn["button_code"]
        )
        if url:
            return {
                "title": title,
                "size":  size,
                "label": btn["label"],
                "url":   url,
            }

    return {"error": "POST ke baad koi valid download URL nahi mila", "status": 502}


# ─────────────────────────── DRIVE ──────────────────────────────

def extract_links_drive(html: str) -> list[dict]:
    """
    Parse plain <a href='...' class='button*'>Label</a> tags.
    """
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
        cls = item["class"]
        try:
            return DRIVE_BUTTON_PRIORITY.index(cls)
        except ValueError:
            return len(DRIVE_BUTTON_PRIORITY)

    results.sort(key=priority)
    return [{"label": r["label"], "url": r["url"]} for r in results]


async def fetch_links_drive(html: str) -> dict:
    title, size = get_title_size(html)
    links = extract_links_drive(html)

    if not links:
        return {"error": "No download links found on drive page", "status": 404}

    first = links[0]
    return {
        "title": title,
        "size":  size,
        "label": first["label"],
        "url":   first["url"],
    }


# ─────────────────────────── MAIN FETCH ─────────────────────────

async def fetch_links(target_url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            target_url, headers=FILESDL_HEADERS, allow_redirects=True
        ) as resp:
            if resp.status != 200:
                return {
                    "error": f"filesdl returned HTTP {resp.status}",
                    "status": resp.status,
                }
            html = await resp.text()

        if "/drive/" in target_url:
            return await fetch_links_drive(html)
        else:
            return await fetch_links_cloud(session, target_url, html)


# ─────────────────────────── HANDLERS ───────────────────────────

async def handle_request(request: web.Request) -> web.Response:
    target_url = request.query.get("url", "").strip()

    if not target_url:
        return web.json_response(
            {"error": "url parameter required. e.g. /?url=https://new1.filesdl.in/cloud/ID"},
            status=400,
        )

    if "filesdl.in" not in target_url:
        return web.json_response(
            {"error": "Only filesdl.in URLs allowed"},
            status=400,
        )

    result = await fetch_links(target_url)

    if "error" in result:
        return web.json_response(result, status=result.get("status", 500))

    return web.json_response(result)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "filesdl-proxy"})


# ─────────────────────────── APP ────────────────────────────────

app = web.Application()
app.router.add_get("/", handle_request)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
