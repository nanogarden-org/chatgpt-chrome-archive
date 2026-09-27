"""Provider definitions and browser-side extraction helpers.

The services intentionally remain DOM-driven: this tool archives what a logged-in
browser renders and never reads cookies, tokens, or private browser storage.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    home: str
    hosts: tuple[str, ...]
    conversation_patterns: tuple[str, ...]
    message_selectors: tuple[str, ...]
    sidebar_selectors: tuple[str, ...]


PROVIDERS = {
    "chatgpt": Provider("chatgpt", "ChatGPT", "https://chatgpt.com/", ("chatgpt.com", "chat.openai.com"), ("/c/",), ('[data-message-author-role="user"]', '[data-message-author-role="assistant"]'), ('a[href^="/c/"]', 'a[href*="chatgpt.com/c/"]')),
    "gemini": Provider("gemini", "Google Gemini", "https://gemini.google.com/", ("gemini.google.com",), ("/app/",), ('user-query', 'model-response', '[data-message-author-role="user"]', '[data-message-author-role="assistant"]'), ('a[href*="/app/"]', 'a[href*="gemini.google.com/app/"]')),
    "claude": Provider("claude", "Anthropic Claude", "https://claude.ai/", ("claude.ai",), ("/chat/",), ('[data-testid="user-message"]', '[data-testid="assistant-message"]', '[data-testid="chat-message"]', 'main article'), ('a[href^="/chat/"]', 'a[href*="claude.ai/chat/"]')),
    "grok": Provider("grok", "X Grok", "https://grok.com/", ("grok.com", "x.com"), ("/c/", "/conversation"), ('[data-testid="message"]', '[data-testid*="message"]', 'main article'), ('a[href*="/c/"]', 'a[href*="conversation"]')),
    "perplexity": Provider("perplexity", "Perplexity", "https://www.perplexity.ai/", ("www.perplexity.ai", "perplexity.ai"), ("/search/", "/thread/"), ('div[class*="user-bubble"] > div[class*="whitespace-pre-wrap"]', '.prose[data-renderer="lm"]', '[data-testid="message"]', '[data-testid*="message"]', 'main article'), ('a[href*="/search/"]', 'a[href*="/thread/"]')),
}


def provider_for_url(url: str) -> Provider:
    host = (urlparse(url).hostname or "").lower()
    for provider in PROVIDERS.values():
        if host in provider.hosts or any(host.endswith("." + h) for h in provider.hosts):
            return provider
    raise ValueError(f"Unsupported provider URL: {url}. Supported: {', '.join(PROVIDERS)}")


def conversation_id(url: str, provider: Provider | None = None) -> str:
    provider = provider or provider_for_url(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    for marker in provider.conversation_patterns:
        if marker in path:
            value = path.split(marker, 1)[1].split("/", 1)[0]
            if value:
                return value
    # Perplexity and Grok sometimes encode the conversation in a query string.
    if parsed.query:
        for part in parsed.query.split("&"):
            key, _, value = part.partition("=")
            if key.lower() in {"conversation", "conversation_id", "thread", "thread_id"} and value:
                return value
    import hashlib
    return "url-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def selector_csv(provider: Provider) -> str:
    return ",".join(provider.message_selectors)


def sidebar_csv(provider: Provider) -> str:
    return ",".join(provider.sidebar_selectors)
