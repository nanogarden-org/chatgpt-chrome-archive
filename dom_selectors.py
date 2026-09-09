"""
DOM discovery helpers kept separate because ChatGPT's frontend may change.
"""

MESSAGE_SELECTORS = [
    '[data-message-author-role="user"]',
    '[data-message-author-role="assistant"]',
]

MESSAGE_ALL = '[data-message-author-role="user"], [data-message-author-role="assistant"]'

# Fallback selectors are intentionally broad and used only if semantic message
# attributes disappear.
FALLBACK_MESSAGE_CANDIDATES = [
    'article',
    '[data-testid*="conversation-turn"]',
    '[class*="conversation-turn"]',
]

SIDEBAR_LINK_SELECTORS = [
    'a[href^="/c/"]',
    'a[href*="chatgpt.com/c/"]',
]

TITLE_SELECTORS = [
    'h1',
    'header h1',
]

SCROLLABLE_SELECTORS = [
    'nav',
    'aside',
    '[class*="sidebar"]',
]
