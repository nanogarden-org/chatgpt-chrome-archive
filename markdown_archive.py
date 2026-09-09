from __future__ import annotations

import json
import re
from pathlib import Path

from utils import json_dump, message_hash, normalize_text, sha256_text, stream_hash

BEGIN_RE = re.compile(
    r"<!-- CHATGPT_ARCHIVE_BEGIN ordinal=(\d+) role=([^\s]+) sha256=([a-f0-9]{64}) -->"
)
END_TEMPLATE = "<!-- CHATGPT_ARCHIVE_END ordinal={ordinal} -->"

def render_markdown(extracted: dict, path: Path) -> None:
    lines = [
        "---",
        f'conversation_id: "{extracted["conversation_id"]}"',
        f'title: {json.dumps(extracted["title"], ensure_ascii=False)}',
        f'url: "{extracted["url"]}"',
        f'captured_at: "{extracted["captured_at"]}"',
        f'message_count: {extracted["message_count"]}',
        f'stream_sha256: "{extracted["stream_sha256"]}"',
        "---",
        "",
        f'# {extracted["title"]}',
        "",
    ]

    for msg in extracted["messages"]:
        role = msg["role"]
        heading = {
            "user": "User",
            "assistant": "Assistant",
            "system": "System",
            "tool": "Tool",
        }.get(role, role.title())

        lines.extend([
            f"## {heading}",
            "",
            f'<!-- CHATGPT_ARCHIVE_BEGIN ordinal={msg["ordinal"]} role={role} sha256={msg["sha256"]} -->',
            msg["text"],
            f'<!-- CHATGPT_ARCHIVE_END ordinal={msg["ordinal"]} -->',
            "",
        ])

        if msg.get("assets"):
            lines.append("### Captured asset references")
            lines.append("")
            for asset in msg["assets"]:
                label = asset.get("label") or asset.get("alt") or asset.get("tag") or "asset"
                target = asset.get("href") or asset.get("src") or ""
                if target:
                    lines.append(f"- {label}: `{target}`")
                else:
                    lines.append(f"- {label}")
            lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

def parse_markdown_messages(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    messages = []
    pos = 0

    while True:
        m = BEGIN_RE.search(text, pos)
        if not m:
            break

        ordinal = int(m.group(1))
        role = m.group(2)
        declared_hash = m.group(3)
        end_marker = END_TEMPLATE.format(ordinal=ordinal)
        end = text.find(end_marker, m.end())
        if end < 0:
            messages.append({
                "ordinal": ordinal,
                "role": role,
                "declared_sha256": declared_hash,
                "text": "",
                "actual_sha256": "",
                "parse_error": f"missing end marker for message {ordinal}",
            })
            break

        body = text[m.end():end]
        # Renderer places one newline after BEGIN and one before END.
        # normalize_text makes the comparison insensitive to those framing newlines
        # while preserving all substantive line structure.
        body = normalize_text(body)
        actual_hash = message_hash(role, body)

        messages.append({
            "ordinal": ordinal,
            "role": role,
            "declared_sha256": declared_hash,
            "text": body,
            "actual_sha256": actual_hash,
            "parse_error": "",
        })
        pos = end + len(end_marker)

    return messages

def verify_markdown(extracted: dict, md_path: Path) -> dict:
    parsed = parse_markdown_messages(md_path)
    source_msgs = extracted["messages"]
    problems = []

    if len(parsed) != len(source_msgs):
        problems.append(
            f"message count mismatch: source={len(source_msgs)} markdown={len(parsed)}"
        )

    reconstructed = []

    for i, (src, md) in enumerate(zip(source_msgs, parsed), 1):
        if md.get("parse_error"):
            problems.append(md["parse_error"])
            continue

        if src["ordinal"] != md["ordinal"]:
            problems.append(
                f"ordinal mismatch at position {i}: "
                f"source={src['ordinal']} markdown={md['ordinal']}"
            )

        if src["role"] != md["role"]:
            problems.append(
                f"role mismatch at message {i}: "
                f"source={src['role']} markdown={md['role']}"
            )

        # Verify both the marker and the actual bytes represented by Markdown text.
        if src["sha256"] != md["declared_sha256"]:
            problems.append(f"declared marker hash mismatch at message {i}")

        if src["sha256"] != md["actual_sha256"]:
            problems.append(f"ACTUAL MARKDOWN CONTENT hash mismatch at message {i}")

        reconstructed.append({
            "role": md["role"],
            "text": md["text"],
        })

    recomputed_source_stream = stream_hash(source_msgs)
    if recomputed_source_stream != extracted["stream_sha256"]:
        problems.append("extracted aggregate stream hash mismatch")

    markdown_stream = stream_hash(reconstructed) if len(reconstructed) == len(source_msgs) else ""
    if markdown_stream and markdown_stream != extracted["stream_sha256"]:
        problems.append("actual Markdown aggregate stream hash mismatch")

    result = {
        "status": "verified" if not problems else "failed",
        "source_message_count": len(source_msgs),
        "markdown_message_count": len(parsed),
        "source_stream_sha256": extracted["stream_sha256"],
        "recomputed_source_stream_sha256": recomputed_source_stream,
        "markdown_stream_sha256": markdown_stream,
        "markdown_file_sha256": sha256_text(md_path.read_text(encoding="utf-8")),
        "problems": problems,
    }
    return result

def verify_folder(folder: Path) -> dict:
    extracted_path = folder / "extracted.json"
    md_path = folder / "conversation.md"

    if not extracted_path.exists():
        return {"status": "failed", "problems": ["missing extracted.json"]}
    if not md_path.exists():
        return {"status": "failed", "problems": ["missing conversation.md"]}

    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
    result = verify_markdown(extracted, md_path)
    json_dump(folder / "verification.json", result)
    return result
