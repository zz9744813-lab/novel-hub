"""v7.4 SafeContentFetcher + SearchProvider protocol.

C-34: Web content is untrusted - SSRF protection, size limits, no JS.
C-32: Research shares GLOBAL_LLM_CONCURRENCY with draft generation.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Protocol
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("novelforge.research.fetcher")

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/json",
}
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 15.0


class SearchProvider(Protocol):
    async def search(self, query: str, *, max_results: int = 5) -> list[dict]:
        ...


class NullSearchProvider:
    """Default no-op provider — research stays off until configured."""

    async def search(self, query: str, *, max_results: int = 5) -> list[dict]:
        return []


class DuckDuckGoLiteProvider:
    """Best-effort HTML search via DuckDuckGo HTML endpoint (no API key).

    Returns [{url, title, snippet}]. Failures degrade to [].
    """

    async def search(self, query: str, *, max_results: int = 5) -> list[dict]:
        import re
        from urllib.parse import unquote, urlparse

        q = (query or "").strip()
        if not q:
            return []
        url = "https://html.duckduckgo.com/html/"
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=TIMEOUT,
                headers={"User-Agent": "NovelForge-Research/7.4"},
            ) as client:
                resp = await client.post(url, data={"q": q})
            if resp.status_code >= 400:
                return []
            html = resp.text
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)
            return []

        # Parse result links: uddg= real URL
        hits: list[dict] = []
        for m in re.finditer(
            r'uddg=([^&"]+)[^>]*>\s*([^<]+)</a>',
            html,
            flags=re.IGNORECASE,
        ):
            try:
                real = unquote(m.group(1))
                title = re.sub(r"\s+", " ", m.group(2)).strip()
            except Exception:
                continue
            parsed = urlparse(real)
            if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
                continue
            if parsed.hostname in {"localhost", "0.0.0.0"}:
                continue
            hits.append({"url": real, "title": title or real, "snippet": ""})
            if len(hits) >= max_results:
                break
        return hits


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(addr in net for net in BLOCKED_NETWORKS)


def resolve_and_check_host(hostname: str) -> list[str]:
    """DNS resolve and reject private/local addresses."""
    infos = socket.getaddrinfo(hostname, None)
    ips = sorted({str(item[4][0]) for item in infos})
    for ip in ips:
        if _is_blocked_ip(ip):
            raise ValueError(f"SSRF blocked: {hostname} resolves to private IP {ip}")
    return ips


async def safe_fetch(url: str) -> dict:
    """Fetch page safely. Returns {url, title, text, content_hash, domain}.

    Does not execute JS. Does not follow more than 3 redirects.
    Re-validates IP after each redirect.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ALLOWED_SCHEMES:
            raise ValueError(f"Scheme not allowed: {parsed.scheme}")
        if not parsed.hostname:
            raise ValueError("Missing hostname")
        if parsed.hostname in {"localhost", "0.0.0.0"}:
            raise ValueError("Localhost blocked")

        resolve_and_check_host(parsed.hostname)

        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=TIMEOUT,
            headers={"User-Agent": "NovelForge-Research/7.4"},
        ) as client:
            resp = await client.get(current)

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                raise ValueError("Redirect without Location")
            # Relative redirect
            if loc.startswith("/"):
                current = f"{parsed.scheme}://{parsed.netloc}{loc}"
            else:
                current = loc
            continue

        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and ctype not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Content-Type not allowed: {ctype}")

        body = resp.content
        if len(body) > MAX_BYTES:
            raise ValueError(f"Page too large: {len(body)} > {MAX_BYTES}")

        text = body.decode(resp.encoding or "utf-8", errors="replace")
        # Strip tags lightly for HTML
        if "html" in ctype:
            import re
            text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
            text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
            text = re.sub(r"(?is)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

        import hashlib
        content_hash = hashlib.sha256(text.encode()).hexdigest()

        return {
            "url": str(resp.url),
            "domain": parsed.hostname,
            "title": None,
            "text": text[:50000],  # hard cap kept in memory only temporarily
            "content_hash": content_hash,
            "wrapped": f"<UNTRUSTED_WEB_CONTENT>\n{text[:20000]}\n</UNTRUSTED_WEB_CONTENT>",
        }

    raise ValueError("Too many redirects")
