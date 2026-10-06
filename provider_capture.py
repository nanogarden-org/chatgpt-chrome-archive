from __future__ import annotations

import asyncio
import logging
import json

from capture_order import CaptureOrder
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import Page

from providers import Provider, conversation_id, selector_csv, sidebar_csv
from utils import json_dump, message_hash, normalize_text, stream_hash

log = logging.getLogger(__name__)


async def _raw_messages(page: Page, provider: Provider) -> list[dict]:
    return await page.evaluate(
        """({selector, provider}) => {
          const nodes = [...document.querySelectorAll(selector)];
          if (!window.__archiveCaptureNodes) {
            window.__archiveCaptureNodes = {ids: new WeakMap(), next: 0};
          }
          const capture = window.__archiveCaptureNodes;
          return nodes.map((node, index) => {
            const attrs = [...node.attributes].map(a => `${a.name}=${a.value}`).join(' ').toLowerCase();
            const tag = node.tagName.toLowerCase();
            const text = node.innerText || node.textContent || '';
            let role = 'unknown';
            if (/user|human|query|prompt/.test(attrs + ' ' + tag)) role = 'user';
            else if (/assistant|model|claude|response|answer|grok|perplexity/.test(attrs + ' ' + tag)) role = 'assistant';
            if (provider === 'perplexity') {
              if (node.closest('[class*="user-bubble"]')) role = 'user';
              else if (node.matches('.prose[data-renderer="lm"]')) role = 'assistant';
            }
            const id = node.getAttribute('data-message-id') || node.id || null;
            const assets = [...node.querySelectorAll('img, video, audio, a[href], a[download]')].map(el => ({
              tag: el.tagName.toLowerCase(), label: (el.innerText || el.alt || '').trim(),
              href: el.getAttribute('href'), src: el.getAttribute('src'), alt: el.getAttribute('alt')
            })).filter(x => x.src || x.href);
            if (!capture.ids.has(node)) capture.ids.set(node, ++capture.next);
            return {role, text, message_id: id, capture_id: capture.ids.get(node), local_index: index, assets};
          });
        }""",
        {"selector": selector_csv(provider), "provider": provider.key},
    )


def _clean(raw: list[dict]) -> list[dict]:
    result = []
    for item in raw:
        text = normalize_text(item.get("text"))
        role = item.get("role")
        if not text or role == "unknown":
            continue
        result.append({**item, "role": role, "text": text})
    return result


async def index_provider(page: Page, provider: Provider, max_passes: int, stable_passes: int) -> list[dict]:
    found = {}
    stable = 0
    for _ in range(max_passes):
        before = len(found)
        for selector in provider.sidebar_selectors:
            for i in range(await page.locator(selector).count()):
                link = page.locator(selector).nth(i)
                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue
                    url = urljoin(provider.home, href)
                    if provider_for_url_safe(url, provider):
                        cid = conversation_id(url, provider)
                        found[cid] = {"provider": provider.key, "conversation_id": cid, "url": url, "title": normalize_text(await link.inner_text()) or cid, "status": ""}
                except Exception:
                    continue
        stable = stable + 1 if len(found) == before and found else 0
        if stable >= stable_passes:
            break
        await page.mouse.wheel(0, 1400)
        await page.wait_for_timeout(500)
    return list(found.values())


def provider_for_url_safe(url: str, provider: Provider) -> bool:
    from providers import provider_for_url
    try:
        return provider_for_url(url).key == provider.key
    except ValueError:
        return False


async def capture_generic(page: Page, provider: Provider, url: str, out: Path) -> dict:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    initial = await page.content()
    (out / "capture-initial.html").write_text(initial, encoding="utf-8")
    (out / "capture-observations.jsonl").write_text("", encoding="utf-8")
    order = CaptureOrder()
    seen = order.messages
    no_new = 0
    reached_top = False
    for step in range(1, 401):
        raw = await _raw_messages(page, provider)
        with (out / "capture-observations.jsonl").open("a", encoding="utf-8") as observations:
            observations.write(json.dumps({"step": step, "messages": raw}, ensure_ascii=False) + "\n")
        if any(normalize_text(item.get("text")) and item.get("role") == "unknown" for item in raw):
            order.problems.add("unrecognized message role")
        batch = _clean(raw)
        before = len(seen)
        order.add(batch)
        no_new = 0 if len(seen) > before else no_new + 1
        top = await page.evaluate("() => (document.scrollingElement?.scrollTop || window.scrollY) <= 3")
        reached_top = reached_top or top
        if top and no_new >= 6:
            break
        await page.evaluate("() => window.scrollTo(0, Math.max(0, (document.scrollingElement?.scrollTop || window.scrollY) - Math.max(500, window.innerHeight * 0.8)))")
        await page.wait_for_timeout(220 if len(seen) > before else 120)
    if not seen:
        raise RuntimeError(f"No {provider.label} messages were visible. Check login or provider adapter selectors.")
    messages = []
    ordered = order.ordered()
    for ordinal, item in enumerate(ordered, 1):
        messages.append({"ordinal": ordinal, "role": item["role"], "text": item["text"], "sha256": message_hash(item["role"], item["text"]), "assets": item.get("assets", []), "message_id": item.get("message_id"), "capture_id": item.get("capture_id")})
    cid = conversation_id(url, provider)
    title = normalize_text(await page.title()) or cid
    final = await page.content()
    (out / "capture-final.html").write_text(final, encoding="utf-8")
    captured_at = datetime.now(timezone.utc).isoformat()
    harvest = {"harvest_version": "provider-generic-v2", "ordering_confirmed": not order.problems, "ordering_problems": sorted(order.problems), "harvest_steps": step, "reached_top": reached_top, "harvested_messages": len(messages), "max_steps": 400, "warning": "" if reached_top else "maximum harvest steps reached before top"}
    extracted = {"provider": provider.key, "conversation_id": cid, "url": url, "title": title, "captured_at": captured_at, "harvest": harvest, "message_count": len(messages), "stream_sha256": stream_hash(messages), "messages": messages}
    json_dump(out / "extracted.json", extracted)
    json_dump(out / "metadata.json", {k: extracted[k] for k in ("provider", "conversation_id", "url", "title", "captured_at", "message_count", "stream_sha256", "harvest")})
    return extracted
