"""
DOM discovery helpers kept separate because ChatGPT's frontend may change.
"""

MESSAGE_SELECTORS = [
    '[data-message-author-role="user"]',
    '[data-message-author-role="assistant"]',
]

MESSAGE_ALL = '[data-message-author-role="user"], [data-message-author-role="assistant"]'

# ChatGPT has recently rendered complete conversations without the semantic
# role attributes above. These containers are a compatibility signal only;
# browser_capture.py still extracts and validates their contents before use.
CONVERSATION_TURN = '[data-testid^="conversation-turn-"]'
MESSAGE_READY = f'{MESSAGE_ALL}, {CONVERSATION_TURN}, article, h4.sr-only'

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
