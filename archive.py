from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from browser_capture import capture_url, index_sidebar, login
from markdown_archive import render_markdown, verify_folder, verify_markdown
from utils import (
    ARCHIVE_DIR,
    LEDGER_DIR,
    conversation_id_from_url,
    json_dump,
    read_index,
    setup_logging,
    upsert_index,
    write_index,
    upsert_ledger,
    write_ledger,
    read_ledger,
)
from providers import provider_for_url, conversation_id as provider_conversation_id

log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def provider_name(value: str | None) -> str:
    return (value or "chatgpt").strip().lower() or "chatgpt"

def archive_folder(provider: str, cid: str):
    provider = provider_name(provider)
    return ARCHIVE_DIR / cid if provider == "chatgpt" else ARCHIVE_DIR / provider / cid

def record_ledger(provider: str, cid: str, extracted=None, verification=None, error=""):
    provider = provider_name(provider)
    folder = archive_folder(provider, cid)
    record = {
        "provider": provider,
        "conversation_id": cid,
        "archive_path": str(folder),
        "working_copy_present": "yes" if folder.exists() else "no",
    }
    if extracted:
        record.update({
            "url": extracted.get("url", ""),
            "title": extracted.get("title", ""),
            "capture_status": "captured",
            "message_count": extracted.get("message_count", ""),
            "captured_at": extracted.get("captured_at", now_iso()),
            "stream_sha256": extracted.get("stream_sha256", ""),
        })
    if verification:
        record.update({
            "verification_status": verification.get("status", "failed"),
            "verified_at": now_iso(),
            "last_error": "; ".join(verification.get("problems", [])) or verification.get("structural_warning", ""),
        })
    if error:
        record["last_error"] = error
        record["capture_status"] = "failed"
    upsert_ledger(record)


def apply_completeness_gate(extracted: dict, verification: dict) -> dict:
    """
    Markdown fidelity is necessary but not sufficient.

    A conversation can only be VERIFIED when browser traversal itself completed.
    This prevents a perfectly-written partial capture from being certified.
    """
    harvest = extracted.get("harvest") or {}
    problems = list(verification.get("problems", []))

    reached_top = harvest.get("reached_top") is True
    warning = (harvest.get("warning") or "").strip()
    harvested = harvest.get("harvested_messages", extracted.get("message_count", 0))
    max_steps = harvest.get("max_steps")
    steps = harvest.get("harvest_steps")

    if not reached_top:
        problems.append(
            "COMPLETENESS GATE: browser traversal did not reach the top of the conversation"
        )

    if warning:
        problems.append(
            f"COMPLETENESS GATE: browser harvester warning: {warning}"
        )

    if not harvested:
        problems.append(
            "COMPLETENESS GATE: zero harvested messages"
        )

    # If both values exist and they are equal while traversal did not settle,
    # make the reason explicit. This is mainly diagnostic.
    if (
        max_steps is not None
        and steps is not None
        and steps >= max_steps
        and not reached_top
    ):
        problems.append(
            f"COMPLETENESS GATE: maximum harvest steps reached ({steps}/{max_steps})"
        )

    # Turn identity is a strong structural integrity signal. Do not hard-fail
    # solely for this because a future ChatGPT DOM change could remove testids,
    # but surface it prominently for manual review.
    structural_warning = ""
    if extracted.get("provider", "chatgpt") == "chatgpt" and harvest.get("all_have_turn_index") is False:
        structural_warning = (
            "Not every harvested message had a ChatGPT conversation-turn index; "
            "capture may require manual review."
        )

    inferred_roles = harvest.get("inferred_role_count", 0)
    if inferred_roles:
        structural_warning = (
            (structural_warning + " " if structural_warning else "")
            + f"{inferred_roles} message role(s) were inferred from rendered turn order; manual review required."
        )

    verification["problems"] = problems
    verification["reached_top"] = reached_top
    verification["harvest_warning"] = warning
    verification["harvested_messages"] = harvested
    verification["structural_warning"] = structural_warning

    verification["status"] = (
        "verified"
        if not problems
        else "failed"
    )

    return verification


def cmd_login(args):
    asyncio.run(login(args.provider))


def cmd_index(args):
    records = asyncio.run(
        index_sidebar(
            max_passes=args.max_passes,
            stable_passes=args.stable_passes,
            provider_key=args.provider,
        )
    )

    upsert_index(records)

    print(
        f"\n[{args.provider.upper()}] Indexed {len(records)} conversations "
        "visible/discoverable in this run."
    )
    print("Saved/merged into index.csv")


def cmd_add(args):
    provider = provider_for_url(args.url)
    cid = conversation_id_from_url(args.url) if provider.key == "chatgpt" else provider_conversation_id(args.url, provider)

    upsert_index([
        {
            "provider": provider.key,
            "conversation_id": cid,
            "url": args.url,
            "title": cid,
            "status": "",
        }
    ])

    print(f"Added {cid}")


async def capture_one(url: str):
    extracted = await capture_url(url)

    folder = archive_folder(extracted.get("provider"), extracted["conversation_id"])

    md_path = folder / "conversation.md"

    render_markdown(
        extracted,
        md_path,
    )

    verification = verify_markdown(
        extracted,
        md_path,
    )

    verification = apply_completeness_gate(
        extracted,
        verification,
    )

    json_dump(
        folder / "verification.json",
        verification,
    )

    record_ledger(extracted.get("provider"), extracted["conversation_id"], extracted, verification)

    return extracted, verification


def update_row_after_capture(
    rows,
    cid,
    extracted=None,
    verification=None,
    error="",
    provider=None,
):
    expected_provider = provider_name(provider) if provider else None
    for row in rows:
        if (
            row.get("conversation_id")
            != cid
        ):
            continue
        if expected_provider and provider_name(row.get("provider")) != expected_provider:
            continue

        if extracted:
            row["title"] = extracted.get(
                "title",
                row.get("title", ""),
            )

            row["message_count"] = str(
                extracted.get(
                    "message_count",
                    "",
                )
            )

            row["captured_at"] = extracted.get(
                "captured_at",
                now_iso(),
            )

            row["stream_sha256"] = extracted.get(
                "stream_sha256",
                "",
            )

        if verification:
            row["status"] = verification.get(
                "status",
                "failed",
            )

            row["error"] = "; ".join(
                verification.get(
                    "problems",
                    [],
                )
            )

            structural = verification.get(
                "structural_warning",
                "",
            )

            if structural:
                row["error"] = (
                    (
                        row["error"] + "; "
                        if row["error"]
                        else ""
                    )
                    + structural
                )

        if error:
            row["status"] = "failed"
            row["error"] = error


def cmd_capture(args):
    rows = read_index()
    selected_provider = provider_name(getattr(args, "provider", "chatgpt"))

    if not rows:
        raise SystemExit(
            "index.csv is empty or missing. "
            "Run: python archive.py index"
        )

    pending = [
        r
        for r in rows
        if (
            r.get("url")
            and provider_name(r.get("provider")) == selected_provider
            and (
                args.retry_verified
                or r.get("status")
                != "verified"
            )
        )
    ]

    print(f"[{selected_provider.upper()}] {len(pending)} conversation(s) pending.")

    for n, row in enumerate(
        pending,
        1,
    ):
        stop_event = getattr(args, "stop_event", None)
        if stop_event is not None and stop_event.is_set():
            print("Stopped safely between conversations. index.csv has been preserved.")
            break

        cid = row["conversation_id"]
        provider = provider_name(row.get("provider"))

        print(
            f"\n[{provider.upper()} {n}/{len(pending)}] "
            f"{row.get('title') or cid}"
        )

        try:
            extracted, verification = asyncio.run(
                capture_one(
                    row["url"]
                )
            )

            update_row_after_capture(
                rows,
                cid,
                extracted,
                verification,
                provider=provider,
            )

            write_index(rows)

            if (
                verification["status"]
                == "verified"
            ):
                print(
                    f"[{provider.upper()}] VERIFIED | "
                    f"{extracted['message_count']} messages | "
                    f"{extracted['title']}"
                )

                if verification.get(
                    "structural_warning"
                ):
                    print(
                        "WARNING | "
                        + verification[
                            "structural_warning"
                        ]
                    )

            else:
                print(
                    f"[{provider.upper()}] FAILED VERIFICATION / COMPLETENESS"
                )

                for p in verification[
                    "problems"
                ]:
                    print(
                        " -",
                        p,
                    )

        except KeyboardInterrupt:
            write_index(rows)

            print(
                "\nStopped safely. "
                "index.csv has been preserved."
            )

            raise

        except Exception as e:
            log.exception(
                "[%s] Capture failed for %s",
                provider,
                cid,
            )

            record_ledger(provider, cid, error=str(e))

            update_row_after_capture(
                rows,
                cid,
                error=str(e),
                provider=provider,
            )

            write_index(rows)

            print(
                f"[{provider.upper()}] CAPTURE FAILED:",
                e,
            )

            if args.stop_on_error:
                raise SystemExit(1)


def cmd_capture_url(args):
    provider = provider_for_url(args.url)
    cid = conversation_id_from_url(args.url) if provider.key == "chatgpt" else provider_conversation_id(args.url, provider)

    upsert_index([
        {
            "provider": provider.key,
            "conversation_id": cid,
            "url": args.url,
            "title": cid,
            "status": "",
        }
    ])

    rows = read_index()

    try:
        extracted, verification = asyncio.run(
            capture_one(
                args.url
            )
        )

        update_row_after_capture(
            rows,
            cid,
            extracted,
            verification,
            provider=provider.key,
        )

        write_index(rows)

        print(
            f"[{provider.key.upper()}] {verification['status'].upper()} | "
            f"{extracted['message_count']} messages | "
            f"{extracted['title']}"
        )

        if verification.get(
            "structural_warning"
        ):
            print(
                "WARNING | "
                + verification[
                    "structural_warning"
                ]
            )

        if verification["problems"]:
            for p in verification[
                "problems"
            ]:
                print(
                    " -",
                    p,
                )

    except Exception as e:
        record_ledger(provider.key, cid, error=str(e))
        update_row_after_capture(
            rows,
            cid,
            error=str(e),
            provider=provider.key,
        )

        write_index(rows)
        raise


def cmd_verify(args):
    """
    Re-audit local Markdown fidelity.

    NOTE: verify_folder checks local source-vs-Markdown fidelity. We then reapply
    the browser completeness gate from extracted.json before certifying.
    """
    rows = read_index()
    by_id = {
        (provider_name(r.get("provider")), r.get("conversation_id")): r
        for r in rows
    }
    provider_totals = {}
    selected_provider = None if getattr(args, "all_providers", False) else provider_name(getattr(args, "provider", "chatgpt"))

    total = 0
    verified = 0
    failed = 0

    folders = sorted(ARCHIVE_DIR.rglob("extracted.json")) if ARCHIVE_DIR.exists() else []
    for extracted_path in folders:
        folder = extracted_path.parent
        if not folder.is_dir():
            continue

        import json
        extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
        provider = provider_name(extracted.get("provider"))
        if selected_provider and provider != selected_provider:
            continue

        total += 1

        result = verify_folder(
            folder
        )

        result = apply_completeness_gate(
            extracted,
            result,
        )

        json_dump(
            folder /
            "verification.json",
            result,
        )

        cid = folder.name
        provider_totals.setdefault(provider, [0, 0])
        provider_totals[provider][0] += 1
        if result["status"] == "verified":
            provider_totals[provider][1] += 1
        record_ledger(provider, cid, extracted, result)

        if (
            result["status"]
            == "verified"
        ):
            verified += 1
        else:
            failed += 1

        if (provider, cid) in by_id:
            by_id[(provider, cid)]["status"] = (
                result["status"]
            )

            by_id[(provider, cid)]["error"] = (
                "; ".join(
                    result.get(
                        "problems",
                        [],
                    )
                )
            )

        print(
            f"[{provider.upper():10}] {result['status'].upper():8} "
            f"{cid} "
            f"{result.get('source_message_count', '?')} "
            "messages"
        )

    write_index(rows)

    print(
        f"\nAudit: "
        f"{verified} verified, "
        f"{failed} failed, "
        f"{total} total."
    )
    for provider, (count, good) in sorted(provider_totals.items()):
        print(f"  {provider}: {good}/{count} verified")

def cmd_ledger(args):
    """Rebuild/update durable per-provider capture ledgers from local archives."""
    import json
    grouped = {}
    count = 0
    for extracted_path in sorted(ARCHIVE_DIR.rglob("extracted.json")) if ARCHIVE_DIR.exists() else []:
        extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
        verification_path = extracted_path.parent / "verification.json"
        verification = json.loads(verification_path.read_text(encoding="utf-8")) if verification_path.exists() else None
        provider = provider_name(extracted.get("provider"))
        folder = extracted_path.parent
        record = {
            "provider": provider,
            "conversation_id": extracted["conversation_id"],
            "url": extracted.get("url", ""),
            "title": extracted.get("title", ""),
            "capture_status": "captured",
            "verification_status": verification.get("status", "") if verification else "",
            "message_count": extracted.get("message_count", ""),
            "captured_at": extracted.get("captured_at", ""),
            "verified_at": "" if not verification else now_iso(),
            "stream_sha256": extracted.get("stream_sha256", ""),
            "archive_path": str(folder),
            "working_copy_present": "yes",
            "backup_status": "",
            "removed_at": "",
            "last_error": "; ".join(verification.get("problems", [])) if verification else "",
        }
        grouped.setdefault(provider, {})[record["conversation_id"]] = record
        count += 1
    for provider, records in grouped.items():
        write_ledger(provider, list(records.values()))
    # Preserve historical rows after the working archive is removed. A later
    # ledger rebuild marks the working copy missing without deleting metadata.
    for provider_csv in LEDGER_DIR.glob("*.csv") if LEDGER_DIR.exists() else []:
        provider = provider_csv.stem
        rows = read_ledger(provider)
        changed = False
        for row in rows:
            archive_path = row.get("archive_path", "")
            present = bool(archive_path) and Path(archive_path).exists()
            value = "yes" if present else "no"
            if row.get("working_copy_present") != value:
                row["working_copy_present"] = value
                changed = True
            if not present and not row.get("removed_at"):
                row["removed_at"] = now_iso()
                changed = True
        if changed:
            write_ledger(provider, rows)
    print(f"Ledger updated for {count} local captures under {LEDGER_DIR}")


def main():
    setup_logging()

    p = argparse.ArgumentParser(
        description=(
            "Capture ChatGPT conversations "
            "through Chrome into verified Markdown."
        )
    )
    p.add_argument("--provider", choices=("chatgpt", "gemini", "claude", "grok", "perplexity"), default="chatgpt")

    sub = p.add_subparsers(
        dest="command",
        required=True,
    )

    s = sub.add_parser(
        "login",
        help=(
            "Open persistent Chrome "
            "profile and log in."
        ),
    )

    s.set_defaults(
        func=cmd_login
    )

    s = sub.add_parser(
        "index",
        help=(
            "Discover conversation URLs "
            "from the ChatGPT sidebar."
        ),
    )

    s.add_argument(
        "--max-passes",
        type=int,
        default=400,
    )

    s.add_argument(
        "--stable-passes",
        type=int,
        default=10,
    )

    s.set_defaults(
        func=cmd_index
    )

    s = sub.add_parser(
        "add",
        help=(
            "Add one conversation URL "
            "to index.csv."
        ),
    )

    s.add_argument(
        "url"
    )

    s.set_defaults(
        func=cmd_add
    )

    s = sub.add_parser(
        "capture",
        help=(
            "Capture all non-verified "
            "rows from index.csv."
        ),
    )

    s.add_argument(
        "--retry-verified",
        action="store_true",
    )

    s.add_argument(
        "--stop-on-error",
        action="store_true",
    )

    s.set_defaults(
        func=cmd_capture
    )

    s = sub.add_parser(
        "capture-url",
        help=(
            "Capture one conversation URL."
        ),
    )

    s.add_argument(
        "url"
    )

    s.set_defaults(
        func=cmd_capture_url
    )

    s = sub.add_parser(
        "verify",
        help=(
            "Re-verify captured Markdown "
            "and browser-completeness evidence."
        ),
    )

    s.set_defaults(
        func=cmd_verify
    )
    s.add_argument(
        "--all-providers",
        action="store_true",
        help="Audit every provider instead of the selected --provider.",
    )

    s = sub.add_parser(
        "ledger",
        help="Rebuild durable per-platform capture ledgers from local archives.",
    )
    s.set_defaults(func=cmd_ledger)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
