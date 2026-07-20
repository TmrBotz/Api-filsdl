"""
FileDL Proxy Server
Cloudflare Worker ke liye filesdl pages fetch karke direct URL return karta hai.
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


async def fetch_direct_url(cloud_type: str, file_id: str) -> dict:
    """
    new1.filesdl.in/cloud/{id} page se direct download URL nikalo.
    Priority 1: Pixeldrain
    Priority 2: Cloud Direct (r2.dev)
    Priority 3: Fast Direct (zdownload/fdownload)
    """
    target = f"https://new1.filesdl.in/{cloud_type}/{file_id}"

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(target, headers=FILESDL_HEADERS, allow_redirects=True) as resp:
            if resp.status != 200:
                return {"error": f"filesdl returned HTTP {resp.status}", "status": resp.status}
            html = await resp.text()

    # Priority 1: Pixeldrain
    pixel = re.search(r'href=\'(https://aws_amzdlbuket\.iwebp\.store/u/([^?\']+)\?download)\'', html)
    if pixel:
        pixel_id = pixel.group(2)
        return {"url": f"https://pixeldrain.dev/api/file/{pixel_id}?download", "method": "Pixeldrain"}

    # Priority 2: Cloud Direct (r2.dev)
    cloud = re.search(r'href=\'([^\']*r2\.dev[^\']+)\'\s*class=\'button2 download-link\'\s*data-id=\'0\'', html)
    if cloud:
        url = cloud.group(1).split("&token=")[0]
        return {"url": url, "method": "Cloud Direct"}

    # Priority 3: Fast Direct (zdownload + fdownload)
    fast = (
        re.search(r'href=\'(https://bbbdownload\.filesdl\.in/(?:fdownload|zdownload)\.php[^\']+)\'', html) or
        re.search(r'href=\'(https://bbdownload\.filesdl\.in/(?:fdownload|zdownload)\.php[^\']+)\'', html)
    )
    if fast:
        return {"url": fast.group(1), "method": "Fast Direct"}

    return {"error": "No download link found", "status": 404}


async def handle_request(request: web.Request) -> web.Response:
    """
    GET /{type}/{id}
    type: cloud ya drive
    """
    cloud_type = request.match_info.get("type")
    file_id    = request.match_info.get("id")

    if cloud_type not in ("cloud", "drive"):
        return web.json_response({"error": "Invalid type — use /cloud/{id} or /drive/{id}"}, status=400)

    result = await fetch_direct_url(cloud_type, file_id)

    if "error" in result:
        status = result.get("status", 500)
        return web.json_response(result, status=status)

    # Direct redirect
    return web.HTTPFound(result["url"])


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "filesdl-proxy"})


app = web.Application()
app.router.add_get("/", handle_health)
app.router.add_get("/health", handle_health)
app.router.add_get("/{type}/{id}", handle_request)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
