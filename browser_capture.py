from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page

from dom_selectors import (
    MESSAGE_ALL,
    SIDEBAR_LINK_SELECTORS,
    SCROLLABLE_SELECTORS,
)
from utils import (
    ARCHIVE_DIR,
    PROFILE_DIR,
    conversation_id_from_url,
    json_dump,
    message_hash,
    normalize_text,
    stream_hash,
)

log = logging.getLogger(__name__)
CHATGPT_URL = "https://chatgpt.com/"


async def launch():
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=False,
        viewport={"width": 1440, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    return pw, context, page


async def login() -> None:
    pw, context, page = await launch()
    try:
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        print("\nChrome is open. Log into ChatGPT normally.")
        print("When you can see your chats, return here and press Enter.\n")
        await asyncio.to_thread(input)
    finally:
        await context.close()
        await pw.stop()


async def find_sidebar_scroll_container(page: Page):
    best = None
    best_count = 0

    for sel in SCROLLABLE_SELECTORS:
        loc = page.locator(sel)
        count = await loc.count()

        for i in range(min(count, 30)):
            el = loc.nth(i)
            try:
                c = await el.locator('a[href*="/c/"]').count()
                if c > best_count:
                    best_count = c
                    best = el
            except Exception:
                pass

    return best


async def index_sidebar(max_passes: int = 400, stable_passes: int = 10) -> list[dict]:
    pw, context, page = await launch()
    found: dict[str, dict] = {}

    try:
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        sidebar = await find_sidebar_scroll_container(page)
        stable = 0

        for pass_no in range(1, max_passes + 1):
            before = len(found)

            for selector in SIDEBAR_LINK_SELECTORS:
                loc = page.locator(selector)

                for i in range(await loc.count()):
                    a = loc.nth(i)

                    try:
                        href = await a.get_attribute("href")
                        if not href or "/c/" not in href:
                            continue

                        url = urljoin(CHATGPT_URL, href)
                        cid = conversation_id_from_url(url)
                        title = normalize_text(await a.inner_text()) or cid

                        found[cid] = {
                            "conversation_id": cid,
                            "url": url,
                            "title": title,
                            "status": "",
                        }
                    except Exception:
                        continue

            now = len(found)
            log.info("Index pass %d: %d conversations discovered", pass_no, now)

            stable = stable + 1 if now == before and now > 0 else 0

            if stable >= stable_passes:
                break

            if sidebar:
                try:
                    await sidebar.evaluate(
                        """el => {
                            el.scrollTop = Math.min(
                                el.scrollHeight,
                                el.scrollTop + Math.max(700, el.clientHeight * 0.9)
                            );
                        }"""
                    )
                except Exception:
                    sidebar = None

            if not sidebar:
                await page.mouse.wheel(0, 1600)

            await page.wait_for_timeout(850)

        return list(found.values())

    finally:
        await context.close()
        await pw.stop()


async def _find_conversation_scroll_target(page: Page) -> dict:
    return await page.evaluate(
        """() => {
            const msgSel =
                '[data-message-author-role="user"],' +
                '[data-message-author-role="assistant"]';

            const candidates = [
                document.scrollingElement,
                ...document.querySelectorAll('*')
            ];

            let best = null;
            let bestScore = -1;

            for (const el of candidates) {
                if (!el) continue;

                const count =
                    el.querySelectorAll ?
                    el.querySelectorAll(msgSel).length :
                    0;

                if (!count) continue;

                const sh = el.scrollHeight || 0;
                const ch = el.clientHeight || window.innerHeight;
                const scrollable = sh > ch + 100;

                const score =
                    count * 1000000 +
                    (scrollable ? sh : 0);

                if (score > bestScore) {
                    bestScore = score;
                    best = el;
                }
            }

            if (
                !best ||
                best === document.scrollingElement ||
                best === document.documentElement ||
                best === document.body
            ) {
                return {kind: 'window'};
            }

            document
                .querySelectorAll('[data-chat-archive-scroll-target="1"]')
                .forEach(el =>
                    el.removeAttribute('data-chat-archive-scroll-target')
                );

            best.setAttribute(
                'data-chat-archive-scroll-target',
                '1'
            );

            return {kind: 'element'};
        }"""
    )


async def _scroll_metrics(page: Page, target: dict) -> dict:
    if target["kind"] == "element":
        return await page.evaluate(
            """() => {
                const el =
                    document.querySelector(
                        '[data-chat-archive-scroll-target="1"]'
                    );

                if (!el) return null;

                return {
                    top: el.scrollTop,
                    height: el.scrollHeight,
                    client: el.clientHeight
                };
            }"""
        )

    return await page.evaluate(
        """() => {
            const el = document.scrollingElement;

            return {
                top: el.scrollTop,
                height: el.scrollHeight,
                client: window.innerHeight
            };
        }"""
    )


async def _set_scroll(page: Page, target: dict, top: float):
    if target["kind"] == "element":
        await page.evaluate(
            """top => {
                const el =
                    document.querySelector(
                        '[data-chat-archive-scroll-target="1"]'
                    );

                if (el) el.scrollTop = top;
            }""",
            top,
        )
    else:
        await page.evaluate(
            "top => window.scrollTo(0, top)",
            top,
        )


async def _extract_dom_batch(page: Page) -> list[dict]:
    """
    Fast path: capture only message identity + visible text.
    Attachment enumeration is intentionally deferred until a message is first
    encountered, avoiding repeated heavy DOM work on the same virtual window.
    """
    return await page.evaluate(
        """() => {
            const nodes = Array.from(
                document.querySelectorAll(
                    '[data-message-author-role="user"],' +
                    '[data-message-author-role="assistant"]'
                )
            );

            function attrFromAncestors(node, name) {
                let el = node;

                for (
                    let i = 0;
                    el && i < 8;
                    i++, el = el.parentElement
                ) {
                    const v =
                        el.getAttribute &&
                        el.getAttribute(name);

                    if (v) return v;
                }

                return null;
            }

            return nodes.map((node, localIndex) => {
                const role =
                    node.getAttribute(
                        'data-message-author-role'
                    ) || 'unknown';

                const text =
                    node.innerText ||
                    node.textContent ||
                    '';

                const testid =
                    attrFromAncestors(
                        node,
                        'data-testid'
                    );

                const messageId =
                    node.getAttribute('data-message-id') ||
                    attrFromAncestors(
                        node,
                        'data-message-id'
                    ) ||
                    node.id ||
                    attrFromAncestors(
                        node,
                        'id'
                    );

                let turnIndex = null;

                if (testid) {
                    const m =
                        testid.match(
                            /conversation-turn-(\\d+)/i
                        );

                    if (m) {
                        turnIndex = Number(m[1]);
                    }
                }

                return {
                    role,
                    text,
                    testid,
                    message_id: messageId,
                    turn_index: turnIndex,
                    local_index: localIndex
                };
            });
        }"""
    )


def _normalize_batch(batch: list[dict]) -> list[dict]:
    out = []

    for item in batch:
        item = dict(item)
        item["text"] = normalize_text(
            item.get("text") or ""
        )
        out.append(item)

    return out


def _identity(item: dict) -> str:
    if item.get("turn_index") is not None:
        return (
            f"turn:{item['turn_index']}:"
            f"{item['role']}"
        )

    if item.get("message_id"):
        return (
            f"id:{item['message_id']}:"
            f"{item['role']}"
        )

    return (
        "hash:" +
        message_hash(
            item["role"],
            item["text"]
        )
    )


async def _extract_assets_for_visible_messages(
    page: Page,
) -> dict[str, list[dict]]:
    """
    Only called when the current viewport actually added new messages.
    Returns asset refs keyed by the same identity rules used by the collector.
    """
    raw = await page.evaluate(
        """() => {
            const nodes = Array.from(
                document.querySelectorAll(
                    '[data-message-author-role="user"],' +
                    '[data-message-author-role="assistant"]'
                )
            );

            function attrFromAncestors(node, name) {
                let el = node;

                for (
                    let i = 0;
                    el && i < 8;
                    i++, el = el.parentElement
                ) {
                    const v =
                        el.getAttribute &&
                        el.getAttribute(name);

                    if (v) return v;
                }

                return null;
            }

            return nodes.map(node => {
                const role =
                    node.getAttribute(
                        'data-message-author-role'
                    ) || 'unknown';

                const text =
                    node.innerText ||
                    node.textContent ||
                    '';

                const testid =
                    attrFromAncestors(
                        node,
                        'data-testid'
                    );

                const messageId =
                    node.getAttribute(
                        'data-message-id'
                    ) ||
                    attrFromAncestors(
                        node,
                        'data-message-id'
                    ) ||
                    node.id ||
                    attrFromAncestors(
                        node,
                        'id'
                    );

                let turnIndex = null;

                if (testid) {
                    const m =
                        testid.match(
                            /conversation-turn-(\\d+)/i
                        );

                    if (m) {
                        turnIndex =
                            Number(m[1]);
                    }
                }

                const assets = [];

                for (
                    const el of
                    node.querySelectorAll(
                        'img, a[download], a[href]'
                    )
                ) {
                    const tag =
                        el.tagName.toLowerCase();

                    const href =
                        el.getAttribute('href');

                    const src =
                        el.getAttribute('src');

                    const alt =
                        el.getAttribute('alt');

                    const label =
                        tag === 'a' ?
                        (el.innerText || '') :
                        (alt || '');

                    if (
                        tag === 'img' ||
                        (
                            href &&
                            (
                                href
                                    .toLowerCase()
                                    .includes('files') ||
                                href
                                    .toLowerCase()
                                    .includes('download') ||
                                href
                                    .toLowerCase()
                                    .includes('backend-api')
                            )
                        )
                    ) {
                        assets.push({
                            tag,
                            label,
                            href,
                            src,
                            alt
                        });
                    }
                }

                return {
                    role,
                    text,
                    testid,
                    message_id: messageId,
                    turn_index: turnIndex,
                    assets
                };
            });
        }"""
    )

    result = {}

    for item in raw:
        item["text"] = normalize_text(
            item.get("text") or ""
        )

        for asset in item.get("assets") or []:
            asset["label"] = normalize_text(
                asset.get("label") or ""
            )

        result[_identity(item)] = (
            item.get("assets") or []
        )

    return result


async def harvest_entire_conversation(
    page: Page,
    max_steps: int = 1600,
    no_new_limit: int = 10,
) -> tuple[list[dict], dict]:
    """
    R2 adaptive collector.

    Strategy
    --------
    1. Start at the bottom.
    2. Harvest currently materialized messages.
    3. Move upward with overlapping adaptive strides.
    4. Speed up aggressively when repeated scans yield no new messages.
    5. Slow down immediately when new turns appear or the virtual document
       height changes.
    6. Once the top is reached, perform quiet confirmation passes.

    This keeps completeness-oriented overlap while avoiding thousands of nearly
    identical scans on huge chats.
    """

    target = await _find_conversation_scroll_target(
        page
    )

    metrics = await _scroll_metrics(
        page,
        target
    )

    if not metrics:
        raise RuntimeError(
            "Could not identify conversation scroll surface."
        )

    await _set_scroll(
        page,
        target,
        metrics["height"],
    )

    await page.wait_for_timeout(900)

    found: dict[str, dict] = {}
    no_new = 0
    reached_top = False
    previous_height = None
    height_changes = 0

    # Multiples of the viewport height.
    # Each mode still leaves overlap with the typical ChatGPT virtual window.
    stride_factor = 1.0

    for step in range(
        1,
        max_steps + 1
    ):
        metrics = await _scroll_metrics(
            page,
            target
        )

        if not metrics:
            raise RuntimeError(
                "Conversation scroll surface disappeared."
            )

        height_changed = (
            previous_height is not None and
            abs(
                metrics["height"] -
                previous_height
            ) > 5
        )

        if height_changed:
            height_changes += 1

        previous_height = metrics["height"]

        batch = _normalize_batch(
            await _extract_dom_batch(
                page
            )
        )

        before = len(found)
        newly_added_ids = []

        for item in batch:
            ident = _identity(item)

            if ident not in found:
                found[ident] = item
                newly_added_ids.append(
                    ident
                )

        added = len(found) - before

        if added > 0:
            # Attachment scanning happens only when a viewport yielded new turns.
            asset_map = (
                await
                _extract_assets_for_visible_messages(
                    page
                )
            )

            for ident in newly_added_ids:
                found[ident]["assets"] = (
                    asset_map.get(
                        ident,
                        []
                    )
                )

            no_new = 0

        else:
            no_new += 1

        top_now = metrics["top"] <= 3

        if top_now:
            reached_top = True

        #
        # Adaptive stride.
        #
        # New content or document height mutation:
        #   slow down / overlap heavily.
        #
        # Long quiet stretches:
        #   jump much farther.
        #
        if top_now:
            stride_factor = 0.35

        elif height_changed or added >= 3:
            stride_factor = 0.70

        elif added > 0:
            stride_factor = 1.00

        elif no_new >= 8:
            stride_factor = 3.25

        elif no_new >= 4:
            stride_factor = 2.40

        elif no_new >= 2:
            stride_factor = 1.70

        else:
            stride_factor = 1.25

        log.info(
            "Harvest %d | "
            "scroll %.0f/%.0f | "
            "DOM %d | "
            "+%d new | "
            "%d total | "
            "stride %.2fx",
            step,
            metrics["top"],
            max(
                0,
                metrics["height"] -
                metrics["client"]
            ),
            len(batch),
            added,
            len(found),
            stride_factor,
        )

        if (
            reached_top and
            no_new >= no_new_limit
        ):
            break

        if top_now:
            #
            # Hold at top. This allows ChatGPT to lazy-load still older content.
            #
            await _set_scroll(
                page,
                target,
                0
            )

            await page.wait_for_timeout(
                650
            )

            continue

        stride_px = max(
            450,
            metrics["client"] *
            stride_factor
        )

        new_top = max(
            0,
            metrics["top"] -
            stride_px
        )

        await _set_scroll(
            page,
            target,
            new_top
        )

        #
        # Dynamic settling:
        # quiet fast jumps need less waiting;
        # content/height changes need a little more.
        #
        if added > 0 or height_changed:
            wait_ms = 260
        elif no_new >= 4:
            wait_ms = 110
        else:
            wait_ms = 170

        await page.wait_for_timeout(
            wait_ms
        )

    if not found:
        raise RuntimeError(
            "No messages were harvested."
        )

    items = list(
        found.values()
    )

    #
    # Order by ChatGPT conversation turn whenever possible.
    #
    indexed = [
        x for x in items
        if x.get("turn_index") is not None
    ]

    unknown = [
        x for x in items
        if x.get("turn_index") is None
    ]

    indexed.sort(
        key=lambda x: (
            x["turn_index"],
            0 if x["role"] == "user" else 1
        )
    )

    items = indexed + unknown

    all_have_turn_index = (
        len(unknown) == 0
    )

    messages = []

    for ordinal, item in enumerate(
        items,
        1
    ):
        messages.append({
            "ordinal": ordinal,
            "role": item["role"],
            "text": item["text"],
            "sha256": message_hash(
                item["role"],
                item["text"]
            ),
            "assets": item.get(
                "assets",
                []
            ),
            "turn_index": item.get(
                "turn_index"
            ),
            "message_id": item.get(
                "message_id"
            ),
            "testid": item.get(
                "testid"
            ),
        })

    harvest_info = {
        "harvest_version": "R2-adaptive",
        "harvest_steps": step,
        "reached_top": reached_top,
        "harvested_messages": len(messages),
        "all_have_turn_index": all_have_turn_index,
        "unknown_identity_count": len(unknown),
        "scroll_height_changes": height_changes,
        "max_steps": max_steps,
        "warning": (
            ""
            if reached_top
            else
            "maximum harvest steps reached before top"
        ),
    }

    return messages, harvest_info


async def get_title(
    page: Page,
    cid: str
) -> str:
    title = normalize_text(
        await page.title()
    )

    for suffix in (
        " | OpenAI",
        " - ChatGPT",
        " | ChatGPT"
    ):
        if title.endswith(suffix):
            title = title[
                :-len(suffix)
            ].strip()

    if (
        not title or
        title.lower() in {
            "chatgpt",
            "openai"
        }
    ):
        title = cid

    return title


async def capture_url(
    url: str
) -> dict:
    cid = conversation_id_from_url(
        url
    )

    out = ARCHIVE_DIR / cid
    out.mkdir(
        parents=True,
        exist_ok=True
    )

    pw, context, page = await launch()

    try:
        log.info(
            "Opening %s",
            url
        )

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        try:
            await page.wait_for_selector(
                MESSAGE_ALL,
                timeout=60000,
            )

        except Exception as e:
            await page.screenshot(
                path=str(
                    out /
                    "capture-failed.png"
                ),
                full_page=True,
            )

            raise RuntimeError(
                "No ChatGPT message containers became visible. "
                "Check login/session or dom_selectors.py."
            ) from e

        await page.wait_for_timeout(
            1200
        )

        initial_html = (
            await page.content()
        )

        (
            out /
            "capture-initial.html"
        ).write_text(
            initial_html,
            encoding="utf-8"
        )

        messages, harvest = (
            await
            harvest_entire_conversation(
                page
            )
        )

        title = await get_title(
            page,
            cid
        )

        final_html = (
            await page.content()
        )

        (
            out /
            "capture-final.html"
        ).write_text(
            final_html,
            encoding="utf-8"
        )

        extracted = {
            "conversation_id": cid,
            "url": url,
            "title": title,
            "captured_at":
                datetime
                .now(timezone.utc)
                .isoformat(),
            "harvest": harvest,
            "message_count":
                len(messages),
            "stream_sha256":
                stream_hash(messages),
            "messages": messages,
        }

        json_dump(
            out /
            "extracted.json",
            extracted,
        )

        metadata = {
            "conversation_id": cid,
            "url": url,
            "title": title,
            "captured_at":
                extracted[
                    "captured_at"
                ],
            "message_count":
                len(messages),
            "stream_sha256":
                extracted[
                    "stream_sha256"
                ],
            "harvest": harvest,
        }

        json_dump(
            out /
            "metadata.json",
            metadata,
        )

        return extracted

    finally:
        await context.close()
        await pw.stop()
