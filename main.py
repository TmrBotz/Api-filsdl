"""
FileDL Proxy Server
GET /?url=https://new1.filesdl.in/cloud/ID
GET /?url=https://new1.filesdl.in/drive/ID
First download link return karta hai JSON mein.
"""

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


def xor_decrypt(p: str, k: str) -> str:
    # Pad to multiple of 4 properly
    def pad(s):
        return s + "=" * (-len(s) % 4)

    a = base64.b64decode(pad(p))
    b = base64.b64decode(pad(k))
    return bytes([v ^ b[i % len(b)] for i, v in enumerate(a)]).decode("utf-8", errors="ignore")


def extract_links(html: str) -> list[dict]:
    results = []
    seen_urls = set()

    # Pattern 1: buttonv2-download-button class wale spans (cloud pages)
    pattern1 = re.compile(
        r'<span[^>]+class=[\'"]([^\'"]*buttonv2-download-button[^\'"]*)[\'"][^>]+'
        r'data-p=[\'"]([^\'"]+)[\'"][^>]+'
        r'data-k=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</span>',
        re.DOTALL
    )

    # Pattern 2: Any element with data-p and data-k attributes (drive pages)
    pattern2 = re.compile(
        r'<(?:span|a|button|div)[^>]+'
        r'data-p=[\'"]([^\'"]+)[\'"][^>]+'
        r'data-k=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</(?:span|a|button|div)>',
        re.DOTALL
    )

    # Pattern 3: data-k aage bhi ho sakta hai data-p se pehle
    pattern3 = re.compile(
        r'<(?:span|a|button|div)[^>]+'
        r'data-k=[\'"]([^\'"]+)[\'"][^>]+'
        r'data-p=[\'"]([^\'"]+)[\'"][^>]*>(.*?)</(?:span|a|button|div)>',
        re.DOTALL
    )

    def process_match(data_p, data_k, inner_html):
        label = re.sub(r'<[^>]+>', '', inner_html).strip()
        try:
            url = xor_decrypt(data_p.strip(), data_k.strip())
        except Exception:
            return
        if not url.startswith("http"):
            return
        if url in seen_urls:
            return
        seen_urls.add(url)
        results.append({"label": label or "Download", "url": url})

    for m in pattern1.finditer(html):
        process_match(m.group(2), m.group(3), m.group(4))

    # Pattern 2 se (p pehle, k baad)
    for m in pattern2.finditer(html):
        process_match(m.group(1), m.group(2), m.group(3))

    # Pattern 3 se (k pehle, p baad)
    for m in pattern3.finditer(html):
        # group(1) = data-k, group(2) = data-p
        process_match(m.group(2), m.group(1), m.group(3))

    return results


def extract_meta(html: str) -> tuple[str, str]:
    """Title aur size extract karo — multiple patterns try karta hai."""

    # Title patterns
    title = ""
    for pat in [
        r"<div[^>]+class=['\"]title['\"][^>]*>([^<]+)</div>",
        r"<div[^>]+class=['\"][^'\"]*title[^'\"]*['\"][^>]*>([^<]+)</div>",
        r"<h1[^>]*>([^<]+)</h1>",
        r"<title>([^<]+)</title>",
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            break

    # Size patterns
    size = ""
    for pat in [
        r"<div[^>]+class=['\"]info['\"][^>]*>Size:\s*([^<]+)</div>",
        r"Size:\s*<[^>]+>([^<]+)<",
        r"Size:\s*([\d.,]+\s*(?:MB|GB|KB))",
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            size = m.group(1).strip()
            break

    return title, size


async def fetch_links(target_url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            target_url,
            headers=FILESDL_HEADERS,
            allow_redirects=True
        ) as resp:
            if resp.status != 200:
                return {
                    "error": f"filesdl returned HTTP {resp.status}",
                    "status": resp.status
                }
            html = await resp.text()

    title, size = extract_meta(html)
    links = extract_links(html)

    if not links:
        # Debug ke liye thoda HTML snippet return karo (production mein hata sakte ho)
        snippet = html[:500].replace("\n", " ")
        return {
            "error": "No download links found",
            "status": 404,
            "debug_snippet": snippet,
        }

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
            {
                "error": (
                    "url parameter required. "
                    "e.g. /?url=https://new1.filesdl.in/cloud/ID"
                )
            },
            status=400,
        )

    # filesdl.in ke saare subdomains allow karo
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


app = web.Application()
app.router.add_get("/", handle_request)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    web.run_app(app, host="0.0.0.0", port=port)
