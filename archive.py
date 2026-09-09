from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from browser_capture import capture_url, index_sidebar, login
from markdown_archive import render_markdown, verify_folder, verify_markdown
from utils import (
    ARCHIVE_DIR,
    conversation_id_from_url,
    json_dump,
    read_index,
    setup_logging,
    upsert_index,
    write_index,
)

log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    if harvest.get("all_have_turn_index") is False:
        structural_warning = (
            "Not every harvested message had a ChatGPT conversation-turn index; "
            "capture may require manual review."
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
    asyncio.run(login())


def cmd_index(args):
    records = asyncio.run(
        index_sidebar(
            max_passes=args.max_passes,
            stable_passes=args.stable_passes,
        )
    )

    upsert_index(records)

    print(
        f"\nIndexed {len(records)} conversations "
        "visible/discoverable in this run."
    )
    print("Saved/merged into index.csv")


def cmd_add(args):
    cid = conversation_id_from_url(args.url)

    upsert_index([
        {
            "conversation_id": cid,
            "url": args.url,
            "title": cid,
            "status": "",
        }
    ])

    print(f"Added {cid}")


async def capture_one(url: str):
    extracted = await capture_url(url)

    folder = (
        ARCHIVE_DIR /
        extracted["conversation_id"]
    )

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

    return extracted, verification


def update_row_after_capture(
    rows,
    cid,
    extracted=None,
    verification=None,
    error="",
):
    for row in rows:
        if (
            row.get("conversation_id")
            != cid
        ):
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
            and (
                args.retry_verified
                or r.get("status")
                != "verified"
            )
        )
    ]

    print(
        f"{len(pending)} conversation(s) pending."
    )

    for n, row in enumerate(
        pending,
        1,
    ):
        stop_event = getattr(args, "stop_event", None)
        if stop_event is not None and stop_event.is_set():
            print("Stopped safely between conversations. index.csv has been preserved.")
            break

        cid = row["conversation_id"]

        print(
            f"\n[{n}/{len(pending)}] "
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
            )

            write_index(rows)

            if (
                verification["status"]
                == "verified"
            ):
                print(
                    "VERIFIED | "
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
                    "FAILED VERIFICATION / COMPLETENESS"
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
                "Capture failed for %s",
                cid,
            )

            update_row_after_capture(
                rows,
                cid,
                error=str(e),
            )

            write_index(rows)

            print(
                "CAPTURE FAILED:",
                e,
            )

            if args.stop_on_error:
                raise SystemExit(1)


def cmd_capture_url(args):
    cid = conversation_id_from_url(
        args.url
    )

    upsert_index([
        {
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
        )

        write_index(rows)

        print(
            f"{verification['status'].upper()} | "
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
        update_row_after_capture(
            rows,
            cid,
            error=str(e),
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
        r.get("conversation_id"): r
        for r in rows
    }

    total = 0
    verified = 0
    failed = 0

    for folder in (
        sorted(
            ARCHIVE_DIR.iterdir()
        )
        if ARCHIVE_DIR.exists()
        else []
    ):
        if not folder.is_dir():
            continue

        total += 1

        extracted_path = (
            folder /
            "extracted.json"
        )

        result = verify_folder(
            folder
        )

        if extracted_path.exists():
            import json

            extracted = json.loads(
                extracted_path.read_text(
                    encoding="utf-8"
                )
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

        if (
            result["status"]
            == "verified"
        ):
            verified += 1
        else:
            failed += 1

        if cid in by_id:
            by_id[cid]["status"] = (
                result["status"]
            )

            by_id[cid]["error"] = (
                "; ".join(
                    result.get(
                        "problems",
                        [],
                    )
                )
            )

        print(
            f"{result['status'].upper():8} "
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


def main():
    setup_logging()

    p = argparse.ArgumentParser(
        description=(
            "Capture ChatGPT conversations "
            "through Chrome into verified Markdown."
        )
    )

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

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
