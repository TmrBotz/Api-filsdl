import re
import base64
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

# Priority order for /drive/ page button classes
DRIVE_BUTTON_PRIORITY = ["button2", "button", "button1", "button4"]

def xor_decrypt(p: str, k: str) -> str:
    a = base64.b64decode(p + "==")
    b = base64.b64decode(k + "==")
    return bytes([v ^ b[i % len(b)] for i, v in enumerate(a)]).decode("utf-8", errors="ignore")


def extract_links_cloud(html: str) -> list[dict]:
    """For /cloud/ pages — uses XOR-encrypted span buttons."""
    results = []
    seen_urls = set()

    pattern = re.compile(
        r'<span[^>]+class=[\'"]([^\'"]*buttonv2-download-button[^\'"]*)[\'"][^>]+'
        r'data-p=[\'"]([^\'"]+)[\'"][^>]+'
        r'data-k=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</span>',
        re.DOTALL
    )

    for m in pattern.finditer(html):
        data_p     = m.group(2).strip()
        data_k     = m.group(3).strip()
        inner_html = m.group(4).strip()
        label = re.sub(r'<[^>]+>', '', inner_html).strip()

        try:
            url = xor_decrypt(data_p, data_k)
        except Exception:
            continue

        if not url.startswith("http") or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append({"label": label, "url": url})

    return results


def extract_links_drive(html: str) -> list[dict]:
    """For /drive/ pages — uses plain <a href class='button*'> tags."""
    results = []
    seen_urls = set()

    pattern = re.compile(
        r"<a\s+href=['\"]([^'\"]+)['\"][^>]+class=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        re.DOTALL
    )

    # Collect all buttons with their class
    found = []
    for m in pattern.finditer(html):
        url       = m.group(1).strip()
        btn_class = m.group(2).strip()
        label     = re.sub(r'<[^>]+>', '', m.group(3)).strip()

        if not url.startswith("http") or url in seen_urls:
            continue
        seen_urls.add(url)
        found.append({"label": label, "url": url, "class": btn_class})

    if not found:
        return []

    # Sort by priority: button2 > button > button1 > button4 > others
    def priority(item):
        cls = item["class"]
        for i, p in enumerate(DRIVE_BUTTON_PRIORITY):
            if cls == p:
                return i
        return len(DRIVE_BUTTON_PRIORITY)

    found.sort(key=priority)
    return [{"label": f["label"], "url": f["url"]} for f in found]


async def fetch_links(target_url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(target_url, headers=FILESDL_HEADERS, allow_redirects=True) as resp:
            if resp.status != 200:
                return {"error": f"filesdl returned HTTP {resp.status}", "status": resp.status}
            html = await resp.text()

    title_m = re.search(r"<div class=['\"]title['\"]>([^<]+)</div>", html)
    title = title_m.group(1).strip() if title_m else ""

    size_m = re.search(r"<div class=['\"]info['\"]>Size:\s*([^<]+)</div>", html)
    size = size_m.group(1).strip() if size_m else ""

    # Detect page type and extract accordingly
    if "/drive/" in target_url:
        links = extract_links_drive(html)
    else:
        links = extract_links_cloud(html)

    if not links:
        return {"error": "No download links found", "status": 404}

    first = links[0]
    return {
        "title": title,
        "size":  size,
        "label": first["label"],
        "url":   first["url"],
    }


async def handle_request(request: web.Request) -> web.Response:
    target_url = request.query.get("url", "").strip()

    if not target_url:
        return web.json_response(
            {"error": "url parameter required. e.g. /?url=https://new1.filesdl.in/cloud/ID"},
            status=400
        )

    if "filesdl.in" not in target_url:
        return web.json_response(
            {"error": "Only filesdl.in URLs allowed"},
            status=400
        )

    result = await fetch_links(target_url)

    if "error" in result:
        return web.json_response(result, status=result.get("status", 500))

    return web.json_response(result)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "filesdl-proxy"})


app = web.Application()
app.router.add_get("/", handle_request)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
