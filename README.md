# AI Conversation Archive

A small Chrome + Playwright tool for capturing conversations from ChatGPT, Google Gemini, Anthropic Claude, X Grok, and Perplexity one at a time into Markdown, with indexing and verification.

## What it does

- Uses real Google Chrome through Playwright.
- Keeps one dedicated persistent Chrome profile in `./profile/` for all providers.
- Uses one persistent Chrome profile for all providers. Log into each service once in the opened Chrome window and let Chrome Password Manager save/autofill the account on later backups.
- Discovers conversation links visible in the sidebar and adds them to `index.csv`.
- Opens chats one at a time.
- Scrolls the conversation toward the beginning until the message count stabilizes.
- Extracts user / assistant messages in order.
- Saves:
  - readable Markdown
  - raw captured HTML
  - metadata JSON
  - verification JSON
- Verifies the saved Markdown against the browser extraction.
- Refuses to mark a chat `verified` if counts or content hashes disagree.
- Can be restarted without recapturing already verified conversations.

## Install

Python 3.10+ recommended.

```powershell
cd chatgpt-chrome-archive
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install
```

`playwright install` installs Playwright browser support. The script itself launches the installed Google Chrome channel (`channel="chrome"`).

## First run

### Desktop GUI (Windows)

Double-click `Start ChatGPT Archive.bat` in this folder. The launcher uses the
existing `.venv` and opens the interface without a terminal window. No additional
GUI dependencies are required; it uses Python's built-in Tkinter, like YT Whisper.

1. Click **1. Log In**, sign in in Chrome, then click **Finish Login** in the GUI.
2. Click **2. Refresh Chat Index** to discover conversations.
3. Choose a provider in the platform dropdown. Then choose **All indexed
   chats for selected platform** or **Single conversation URL**, and click
   **3. Start Capture**. Verified chats are skipped in batch mode by default.
4. Click **4. Verify Archive** to re-audit saved conversations for the selected
   platform locally.

Search the conversation list to find a chat. Selecting a row fills the URL field
and shows capture errors, if present. Double-click a row or use **Open Markdown**
to open its saved conversation with your Windows default application.
**Add to Index** queues a URL without capturing it.

**Stop After Current Chat** requests a safe batch stop: the active conversation
finishes and saves before the next one is skipped. Indexing, single captures,
and verification finish their current operation before the window can close.
The live log and per-conversation statuses report results; a finished process
does not mean every conversation passed verification.

The GUI uses the same `archive/`, `profile/`, `index.csv`, and `logs/` as the CLI.

The viewer and command output identify the provider for every capture and
validation event. The conversation table can be filtered by platform, and
the **Open Ledgers** button opens durable per-provider capture ledgers under
`ledger/`. Each ledger retains the conversation URL, title, capture and
verification status, message count, stream hash, archive path, and whether
the working copy is still present. The tool does not delete captures or mark
backups automatically; after you independently confirm a backup, the ledger
is the place to retain the capture record when the local working folder is
removed.
Run only one GUI or CLI operation at a time because they share this profile and
index. The command-line workflow below remains available.

The platform selection applies to batch capture and validation. A deliberate
whole-archive validation is available from the CLI:

```powershell
.\.venv\Scripts\python.exe archive.py --provider chatgpt verify --all-providers
```

Close any Chrome window that is using this tool's dedicated `profile/` directory.

```powershell
.\.venv\Scripts\python.exe archive.py login
```

A Chrome window opens. Log into ChatGPT. When your account is usable, return to the terminal and press Enter.

This login is saved only in the local `profile/` directory.

## Build / refresh the conversation index

```powershell
.\.venv\Scripts\python.exe archive.py index
```

The script opens ChatGPT and repeatedly scans visible sidebar conversation links while scrolling the sidebar. It adds newly found conversations to:

```text
index.csv
```

Run `index` again later if you suspect the sidebar did not expose everything in one pass. Existing rows are preserved.

You can also manually add a conversation URL:

```powershell
.\.venv\Scripts\python.exe archive.py add "https://chatgpt.com/c/CONVERSATION-ID"
```

## Capture everything indexed

```powershell
.\.venv\Scripts\python.exe archive.py capture
```

Only rows not already marked `verified` are processed.

For each conversation:

1. Open URL.
2. Wait for message containers.
3. Repeatedly scroll toward the top / beginning.
4. Wait until the extracted message count stabilizes.
5. Extract every recognized user / assistant message.
6. Save browser HTML and normalized message data.
7. Render Markdown.
8. Re-read the Markdown.
9. Compare message count and normalized SHA-256 fingerprint.
10. Mark the index row `verified` only when both agree.

## Capture a single URL

```powershell
.\.venv\Scripts\python.exe archive.py capture-url "https://chatgpt.com/c/CONVERSATION-ID"
```

## Other providers

The same workflow is supported for Gemini, Claude, Grok, and Perplexity. Choose a provider in the GUI, or pass it before the command in the CLI:

```powershell
.\.venv\Scripts\python.exe archive.py --provider gemini login
.\.venv\Scripts\python.exe archive.py --provider gemini index
.\.venv\Scripts\python.exe archive.py add "https://gemini.google.com/app/CONVERSATION-ID"
.\.venv\Scripts\python.exe archive.py capture-url "https://claude.ai/chat/CONVERSATION-ID"
.\.venv\Scripts\python.exe archive.py capture-url "https://grok.com/c/CONVERSATION-ID"
.\.venv\Scripts\python.exe archive.py capture-url "https://www.perplexity.ai/search/THREAD-ID"
```

`capture-url` infers its provider from the hostname. Provider captures are stored under `archive/<provider>/<conversation-id>/`; existing ChatGPT captures stay in the original `archive/<conversation-id>/` layout. Each capture preserves the browser-rendered HTML, normalized message JSON, Markdown, asset references, and verification metadata. Protected binary attachments are referenced but are not downloaded automatically.

### Login and saved passwords

The tool deliberately does not import or parse a password CSV, and it never stores credentials in the archive. Keep `Chrome Passwords.csv` private because it contains plaintext passwords. If those credentials are not already in Chrome Password Manager, import the CSV through Chrome's own Password Manager UI into the same profile used by this tool, then run:

```powershell
.\.venv\Scripts\python.exe archive.py --provider gemini login
```

Chrome may autofill the saved username and password. If it asks for a password-manager confirmation, complete that in Chrome. The tool pauses until you press Enter after the provider account is visibly logged in. Grok's login command also clicks its initial visible `Sign in`/`Log in` gate when present; choose the Google account and complete any validation yourself. Repeat the one-time login flow for Claude, Grok, and Perplexity; later runs reuse the persistent profile.

Do not place the CSV in the project directory or commit it to source control. If you previously used the provider-specific profile folders created by an earlier version, the current release intentionally uses the root `profile/` directory so all saved credentials and sessions are available together; the old folders can be left untouched until you confirm the new profile works.

The non-ChatGPT adapters use conservative DOM heuristics because these interfaces change frequently. If a provider reports zero messages or an unexpected role/order, inspect its saved HTML and update only `providers.py` or `provider_capture.py`; a partial capture must not be treated as complete.

## Audit the archive again

```powershell
.\.venv\Scripts\python.exe archive.py verify
```

This re-checks all local conversation folders without visiting ChatGPT.

To rebuild the provider ledgers from the local archive folders:

```powershell
.\.venv\Scripts\python.exe archive.py ledger
```

This writes one CSV per platform, such as `ledger/chatgpt.csv`,
`ledger/gemini.csv`, and `ledger/claude.csv`. The command is safe to repeat.

## Output structure

```text
archive/
  <conversation-id>/
    conversation.md
    capture.html
    extracted.json
    metadata.json
    verification.json

index.csv
logs/
  archive.log
profile/
```

## Verification model

The tool intentionally verifies a *normalized message stream* rather than just checking whether a file exists.

For each message it records:

- ordinal number
- role
- normalized visible text

A SHA-256 fingerprint is then calculated over the ordered stream:

```text
1|user|...
2|assistant|...
3|user|...
```

The Markdown surrounds every captured message body with verification boundaries:

```html
<!-- CHATGPT_ARCHIVE_BEGIN ordinal=1 role=user sha256=... -->
actual captured message text
<!-- CHATGPT_ARCHIVE_END ordinal=1 -->
```

During verification, the program re-opens `conversation.md`, extracts the **actual text between those boundaries**, normalizes it exactly as the browser capture was normalized, and calculates the hashes again. It does not trust the stored marker alone.

A conversation passes only if:

- extracted message count == Markdown message count
- message roles and order match
- the marker hash matches
- the actual Markdown message body's normalized SHA-256 matches
- the actual reconstructed Markdown message stream hash matches the browser-extracted stream hash

## Important limitations

### Images and uploaded files

The readable Markdown records image / attachment labels and URLs that are present in the DOM, but this R0 tool does **not** automatically download protected binary attachments. The raw HTML is preserved so those references are not discarded.

### Regenerated / branched replies

The browser shows the currently selected visible branch. This tool archives what Chrome renders. If you changed branches manually, those alternate branches need separate capture work.

### ChatGPT UI changes

The extractor primarily uses `data-message-author-role`, which has historically been a useful semantic attribute. It also includes fallback heuristics. If OpenAI changes the DOM substantially, `dom_selectors.py` is the one small file intended for repair.

### Completeness

`verified` means:

> everything the browser extractor saw was written faithfully to Markdown.

It cannot prove that ChatGPT's browser UI itself successfully loaded data that the server never supplied. For this reason the tool also saves raw HTML and records the number of loading passes performed.

When a capture reports a suspiciously small count or a timeout, do not treat it as complete.

## Recommended workflow

\\ *powershell*
.\.venv\Scripts\python.exe archive.py login
.\.venv\Scripts\python.exe archive.py index
.\.venv\Scripts\python.exe archive.py capture
.\.venv\Scripts\python.exe archive.py verify
\\

Then rerun:

```powershell
.\.venv\Scripts\python.exe archive.py index

```

until no additional conversations are discovered.

For irreplaceable chats, visually open a few long examples and compare the beginning and ending messages against the Markdown before trusting unattended capture.
