from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ARCHIVE_DIR = ROOT / "archive"
PROFILE_DIR = ROOT / "profile"
LOG_DIR = ROOT / "logs"
INDEX_FILE = ROOT / "index.csv"

INDEX_FIELDS = [
    "conversation_id",
    "url",
    "title",
    "status",
    "message_count",
    "captured_at",
    "stream_sha256",
    "error",
]

def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "archive.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def conversation_id_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    m = re.search(r"/c/([^/?#]+)", path)
    if not m:
        raise ValueError(f"Not a ChatGPT conversation URL: {url}")
    return m.group(1)

def safe_filename(value: str, limit: int = 100) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "untitled")[:limit]

def message_hash(role: str, text: str) -> str:
    return sha256_text(f"{role}\n{normalize_text(text)}")

def stream_hash(messages: list[dict]) -> str:
    pieces = []
    for i, msg in enumerate(messages, 1):
        pieces.append(f"{i}|{msg['role']}|{normalize_text(msg['text'])}")
    return sha256_text("\n\x1e\n".join(pieces))

def read_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    with INDEX_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_index(rows: list[dict]) -> None:
    with INDEX_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in INDEX_FIELDS})

def upsert_index(records: list[dict]) -> None:
    rows = read_index()
    by_id = {r["conversation_id"]: r for r in rows if r.get("conversation_id")}
    for rec in records:
        cid = rec["conversation_id"]
        if cid in by_id:
            old = by_id[cid]
            for k, v in rec.items():
                if v not in (None, ""):
                    old[k] = v
        else:
            base = {k: "" for k in INDEX_FIELDS}
            base.update(rec)
            rows.append(base)
            by_id[cid] = base
    write_index(rows)

def json_dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
