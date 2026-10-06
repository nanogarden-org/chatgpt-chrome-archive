import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import AsyncMock
from providers import PROVIDERS
from provider_capture import capture_generic
from unittest.mock import patch
from capture_order import CaptureOrder
from provider_capture import _clean
from archive import apply_completeness_gate
import utils


def message(identity, text="same"):
    return {"capture_id": identity, "role": "user", "text": text}


class CaptureTests(unittest.TestCase):
    def test_repeated_text_is_preserved(self):
        batch = _clean([message(1), message(2)])
        capture = CaptureOrder()
        capture.add(batch)
        self.assertEqual(len(capture.ordered()), 2)
        self.assertFalse(capture.problems)

    def test_older_snapshot_precedes_newer(self):
        capture = CaptureOrder()
        capture.add([message(3), message(4)])
        capture.add([message(1), message(2), message(3)])
        self.assertEqual([m["capture_id"] for m in capture.ordered()], [1, 2, 3, 4])
        self.assertFalse(capture.problems)

    def test_disconnected_and_conflicting_order_are_unconfirmed(self):
        for batches in ([[message(1)], [message(2)]], [[message(1), message(2)], [message(2), message(1)]]):
            capture = CaptureOrder()
            for batch in batches:
                capture.add(batch)
            self.assertEqual(len(capture.ordered()), 2)
            self.assertTrue(capture.problems)

    def test_mutating_and_missing_identity_are_unconfirmed(self):
        capture = CaptureOrder()
        capture.add([message(1)])
        capture.add([message(1, "changed"), {"role": "user", "text": "anonymous"}])
        capture.ordered()
        self.assertEqual(len(capture.problems), 2)

    def test_provider_scoped_index_and_legacy_chatgpt(self):
        rows = [{"conversation_id": "shared", "title": "old"}]
        with patch.object(utils, "read_index", return_value=rows), patch.object(utils, "write_index") as write:
            utils.upsert_index([{"provider": "claude", "conversation_id": "shared", "title": "claude"},
                                {"provider": "chatgpt", "conversation_id": "shared", "title": "updated"}])
        self.assertEqual([r["title"] for r in write.call_args.args[0]], ["updated", "claude"])

    def test_fidelity_does_not_certify_unordered_capture(self):
        extracted = {"provider": "claude", "message_count": 2, "harvest": {"reached_top": True}}
        result = apply_completeness_gate(extracted, {"problems": []})
        self.assertNotEqual(result["status"], "verified")
        extracted["harvest"]["ordering_confirmed"] = True
        self.assertEqual(apply_completeness_gate(extracted, {"problems": []})["status"], "verified")


class CaptureIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_writes_chronological_messages_and_observations(self):
        page = AsyncMock()
        page.content.return_value = "<html>synthetic</html>"
        page.title.return_value = "synthetic"
        page.evaluate.return_value = True
        snapshots = [[message(3), message(4)], [message(1), message(2), message(3)]]
        snapshots += [snapshots[-1]] * 6
        with tempfile.TemporaryDirectory() as folder, patch("provider_capture._raw_messages", side_effect=snapshots):
            result = await capture_generic(page, PROVIDERS["claude"], "https://claude.ai/chat/synthetic", Path(folder))
            self.assertEqual(result["message_count"], 4)
            self.assertEqual([m["ordinal"] for m in result["messages"]], [1, 2, 3, 4])
            self.assertEqual([m["capture_id"] for m in result["messages"]], [1, 2, 3, 4])
            self.assertTrue(result["harvest"]["ordering_confirmed"])
            observations = [json.loads(line) for line in (Path(folder) / "capture-observations.jsonl").read_text().splitlines()]
            self.assertEqual(len(observations), 8)
            self.assertEqual(len(observations[0]["messages"]), 2)
