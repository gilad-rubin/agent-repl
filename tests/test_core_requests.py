"""Tests for collaboration, runtime, and document request models."""
from __future__ import annotations

import unittest

from agent_repl.core.collaboration_requests import (
    BranchFinishRequest,
    BranchReviewRequestRequest,
    BranchReviewResolveRequest,
    BranchStartRequest,
    LeaseAcquireRequest,
    LeaseReleaseRequest,
    PresenceClearRequest,
    PresenceUpsertRequest,
    SessionDetachRequest,
    SessionEndRequest,
    SessionResolveRequest,
    SessionStartRequest,
    SessionTouchRequest,
)
from agent_repl.core.document_requests import (
    DocumentOpenRequest,
    DocumentRebindRequest,
    DocumentRefreshRequest,
)
from agent_repl.core.runtime_requests import (
    RunFinishRequest,
    RunStartRequest,
    RuntimeDiscardRequest,
    RuntimePromoteRequest,
    RuntimeRecoverRequest,
    RuntimeStartRequest,
    RuntimeStopRequest,
)


# ---- Collaboration request tests ----


class TestSessionStartRequest(unittest.TestCase):
    def test_round_trips_all_fields(self):
        request = SessionStartRequest.from_payload(
            {
                "actor": "human",
                "client": "vscode",
                "session_id": "sess-1",
                "label": "my-session",
                "capabilities": ["edit", "execute"],
            }
        )
        self.assertEqual(request.actor, "human")
        self.assertEqual(request.client, "vscode")
        self.assertEqual(request.session_id, "sess-1")
        self.assertEqual(request.label, "my-session")
        self.assertEqual(request.capabilities, ["edit", "execute"])
        self.assertEqual(
            request.to_payload(),
            {
                "actor": "human",
                "client": "vscode",
                "session_id": "sess-1",
                "label": "my-session",
                "capabilities": ["edit", "execute"],
            },
        )

    def test_filters_non_string_capabilities(self):
        request = SessionStartRequest.from_payload(
            {"actor": "human", "client": "cli", "session_id": "s1", "capabilities": ["edit", 3, "", None]}
        )
        self.assertEqual(request.capabilities, ["edit"])

    def test_missing_actor_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing actor"):
            SessionStartRequest.from_payload({"client": "cli", "session_id": "s1"})

    def test_missing_client_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing client"):
            SessionStartRequest.from_payload({"actor": "human", "session_id": "s1"})

    def test_missing_session_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing session_id"):
            SessionStartRequest.from_payload({"actor": "human", "client": "cli"})

    def test_optional_fields_default_to_none(self):
        request = SessionStartRequest.from_payload({"actor": "human", "client": "cli", "session_id": "s1"})
        self.assertIsNone(request.label)
        self.assertIsNone(request.capabilities)


class TestSessionResolveRequest(unittest.TestCase):
    def test_defaults_to_human(self):
        request = SessionResolveRequest.from_payload({})
        self.assertEqual(request.actor, "human")

    def test_accepts_custom_actor(self):
        request = SessionResolveRequest.from_payload({"actor": "agent"})
        self.assertEqual(request.actor, "agent")


class TestSessionIdOnlyRequests(unittest.TestCase):
    def test_touch_requires_session_id(self):
        with self.assertRaisesRegex(ValueError, "Missing session_id"):
            SessionTouchRequest.from_payload({})

    def test_detach_requires_session_id(self):
        with self.assertRaisesRegex(ValueError, "Missing session_id"):
            SessionDetachRequest.from_payload({})

    def test_end_requires_session_id(self):
        with self.assertRaisesRegex(ValueError, "Missing session_id"):
            SessionEndRequest.from_payload({})

    def test_touch_round_trips(self):
        request = SessionTouchRequest.from_payload({"session_id": "s1"})
        self.assertEqual(request.to_payload(), {"session_id": "s1"})

    def test_detach_round_trips(self):
        request = SessionDetachRequest.from_payload({"session_id": "s1"})
        self.assertEqual(request.to_payload(), {"session_id": "s1"})

    def test_end_round_trips(self):
        request = SessionEndRequest.from_payload({"session_id": "s1"})
        self.assertEqual(request.to_payload(), {"session_id": "s1"})


class TestPresenceUpsertRequest(unittest.TestCase):
    def test_round_trips_with_optional_fields(self):
        request = PresenceUpsertRequest.from_payload(
            {"session_id": "s1", "path": "nb.ipynb", "activity": "editing", "cell_id": "c1", "cell_index": 3}
        )
        self.assertEqual(request.cell_id, "c1")
        self.assertEqual(request.cell_index, 3)
        payload = request.to_payload()
        self.assertEqual(payload["cell_id"], "c1")
        self.assertEqual(payload["cell_index"], 3)

    def test_missing_required_fields_raise(self):
        with self.assertRaisesRegex(ValueError, "Missing session_id"):
            PresenceUpsertRequest.from_payload({"path": "nb.ipynb", "activity": "editing"})
        with self.assertRaisesRegex(ValueError, "Missing path"):
            PresenceUpsertRequest.from_payload({"session_id": "s1", "activity": "editing"})
        with self.assertRaisesRegex(ValueError, "Missing activity"):
            PresenceUpsertRequest.from_payload({"session_id": "s1", "path": "nb.ipynb"})


class TestPresenceClearRequest(unittest.TestCase):
    def test_path_is_optional(self):
        request = PresenceClearRequest.from_payload({"session_id": "s1"})
        self.assertIsNone(request.path)
        self.assertEqual(request.to_payload(), {"session_id": "s1"})

    def test_path_included_when_present(self):
        request = PresenceClearRequest.from_payload({"session_id": "s1", "path": "nb.ipynb"})
        self.assertEqual(request.path, "nb.ipynb")


class TestBranchStartRequest(unittest.TestCase):
    def test_round_trips_all_fields(self):
        request = BranchStartRequest.from_payload(
            {
                "branch_id": "b1",
                "document_id": "d1",
                "owner_session_id": "s1",
                "parent_branch_id": "b0",
                "title": "fix",
                "purpose": "bugfix",
            }
        )
        self.assertEqual(request.branch_id, "b1")
        self.assertEqual(request.purpose, "bugfix")
        payload = request.to_payload()
        self.assertEqual(payload["title"], "fix")

    def test_missing_branch_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing branch_id"):
            BranchStartRequest.from_payload({"document_id": "d1"})

    def test_missing_document_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing document_id"):
            BranchStartRequest.from_payload({"branch_id": "b1"})


class TestBranchFinishRequest(unittest.TestCase):
    def test_round_trips(self):
        request = BranchFinishRequest.from_payload({"branch_id": "b1", "status": "merged"})
        self.assertEqual(request.to_payload(), {"branch_id": "b1", "status": "merged"})

    def test_missing_status_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing status"):
            BranchFinishRequest.from_payload({"branch_id": "b1"})


class TestBranchReviewRequestRequest(unittest.TestCase):
    def test_note_is_optional(self):
        request = BranchReviewRequestRequest.from_payload(
            {"branch_id": "b1", "requested_by_session_id": "s1"}
        )
        self.assertIsNone(request.note)

    def test_missing_requested_by_session_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing requested_by_session_id"):
            BranchReviewRequestRequest.from_payload({"branch_id": "b1"})


class TestBranchReviewResolveRequest(unittest.TestCase):
    def test_round_trips_with_note(self):
        request = BranchReviewResolveRequest.from_payload(
            {"branch_id": "b1", "resolved_by_session_id": "s1", "resolution": "approved", "note": "lgtm"}
        )
        self.assertEqual(request.note, "lgtm")
        self.assertEqual(request.resolution, "approved")

    def test_missing_resolution_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing resolution"):
            BranchReviewResolveRequest.from_payload(
                {"branch_id": "b1", "resolved_by_session_id": "s1"}
            )


class TestLeaseRequests(unittest.TestCase):
    def test_acquire_defaults_kind_to_edit(self):
        request = LeaseAcquireRequest.from_payload({"session_id": "s1", "resource_id": "r1"})
        self.assertEqual(request.kind, "edit")

    def test_acquire_missing_resource_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing resource_id"):
            LeaseAcquireRequest.from_payload({"session_id": "s1"})

    def test_release_round_trips(self):
        request = LeaseReleaseRequest.from_payload({"session_id": "s1", "resource_id": "r1"})
        self.assertEqual(request.to_payload(), {"session_id": "s1", "resource_id": "r1"})

    def test_release_missing_session_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing session_id"):
            LeaseReleaseRequest.from_payload({"resource_id": "r1"})


# ---- Runtime request tests ----


class TestRuntimeStartRequest(unittest.TestCase):
    def test_round_trips_all_fields(self):
        request = RuntimeStartRequest.from_payload(
            {
                "runtime_id": "rt-1",
                "mode": "headless",
                "label": "worker",
                "environment": "prod",
                "document_path": "nb.ipynb",
                "ttl_seconds": 300,
            }
        )
        self.assertEqual(request.runtime_id, "rt-1")
        self.assertEqual(request.mode, "headless")
        self.assertEqual(request.ttl_seconds, 300)
        payload = request.to_payload()
        self.assertEqual(payload["label"], "worker")

    def test_invalid_mode_raises(self):
        with self.assertRaisesRegex(ValueError, "Invalid mode"):
            RuntimeStartRequest.from_payload({"runtime_id": "rt-1", "mode": "bogus"})

    def test_missing_runtime_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing runtime_id"):
            RuntimeStartRequest.from_payload({"mode": "shared"})

    def test_optional_fields_default_to_none(self):
        request = RuntimeStartRequest.from_payload({"runtime_id": "rt-1", "mode": "shared"})
        self.assertIsNone(request.label)
        self.assertIsNone(request.environment)
        self.assertIsNone(request.document_path)
        self.assertIsNone(request.ttl_seconds)


class TestRuntimeIdOnlyRequests(unittest.TestCase):
    def test_stop_requires_runtime_id(self):
        with self.assertRaisesRegex(ValueError, "Missing runtime_id"):
            RuntimeStopRequest.from_payload({})

    def test_recover_requires_runtime_id(self):
        with self.assertRaisesRegex(ValueError, "Missing runtime_id"):
            RuntimeRecoverRequest.from_payload({})

    def test_discard_requires_runtime_id(self):
        with self.assertRaisesRegex(ValueError, "Missing runtime_id"):
            RuntimeDiscardRequest.from_payload({})

    def test_stop_round_trips(self):
        request = RuntimeStopRequest.from_payload({"runtime_id": "rt-1"})
        self.assertEqual(request.to_payload(), {"runtime_id": "rt-1"})


class TestRuntimePromoteRequest(unittest.TestCase):
    def test_defaults_mode_to_shared(self):
        request = RuntimePromoteRequest.from_payload({"runtime_id": "rt-1"})
        self.assertEqual(request.mode, "shared")

    def test_invalid_promote_mode_raises(self):
        with self.assertRaisesRegex(ValueError, "Invalid mode"):
            RuntimePromoteRequest.from_payload({"runtime_id": "rt-1", "mode": "headless"})

    def test_accepts_pinned(self):
        request = RuntimePromoteRequest.from_payload({"runtime_id": "rt-1", "mode": "pinned"})
        self.assertEqual(request.mode, "pinned")


class TestRunStartRequest(unittest.TestCase):
    def test_round_trips(self):
        request = RunStartRequest.from_payload(
            {
                "run_id": "r1",
                "runtime_id": "rt-1",
                "target_type": "document",
                "target_ref": "nb.ipynb",
                "kind": "execute-all",
            }
        )
        self.assertEqual(request.run_id, "r1")
        self.assertEqual(request.target_type, "document")
        self.assertEqual(
            request.to_payload(),
            {
                "run_id": "r1",
                "runtime_id": "rt-1",
                "target_type": "document",
                "target_ref": "nb.ipynb",
                "kind": "execute-all",
            },
        )

    def test_invalid_target_type_raises(self):
        with self.assertRaisesRegex(ValueError, "Invalid target_type"):
            RunStartRequest.from_payload(
                {"run_id": "r1", "runtime_id": "rt-1", "target_type": "bogus", "target_ref": "x", "kind": "k"}
            )

    def test_missing_run_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing run_id"):
            RunStartRequest.from_payload(
                {"runtime_id": "rt-1", "target_type": "document", "target_ref": "x", "kind": "k"}
            )

    def test_missing_kind_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing kind"):
            RunStartRequest.from_payload(
                {"run_id": "r1", "runtime_id": "rt-1", "target_type": "document", "target_ref": "x"}
            )


class TestRunFinishRequest(unittest.TestCase):
    def test_round_trips(self):
        request = RunFinishRequest.from_payload({"run_id": "r1", "status": "completed"})
        self.assertEqual(request.to_payload(), {"run_id": "r1", "status": "completed"})

    def test_missing_status_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing status"):
            RunFinishRequest.from_payload({"run_id": "r1"})

    def test_missing_run_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing run_id"):
            RunFinishRequest.from_payload({"status": "completed"})


# ---- Document request tests ----


class TestDocumentOpenRequest(unittest.TestCase):
    def test_round_trips(self):
        request = DocumentOpenRequest.from_payload({"path": "nb.ipynb"})
        self.assertEqual(request.path, "nb.ipynb")
        self.assertEqual(request.to_payload(), {"path": "nb.ipynb"})

    def test_missing_path_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing path"):
            DocumentOpenRequest.from_payload({})


class TestDocumentRefreshRequest(unittest.TestCase):
    def test_round_trips(self):
        request = DocumentRefreshRequest.from_payload({"document_id": "doc-1"})
        self.assertEqual(request.to_payload(), {"document_id": "doc-1"})

    def test_missing_document_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing document_id"):
            DocumentRefreshRequest.from_payload({})


class TestDocumentRebindRequest(unittest.TestCase):
    def test_round_trips(self):
        request = DocumentRebindRequest.from_payload({"document_id": "doc-1"})
        self.assertEqual(request.to_payload(), {"document_id": "doc-1"})

    def test_missing_document_id_raises(self):
        with self.assertRaisesRegex(ValueError, "Missing document_id"):
            DocumentRebindRequest.from_payload({})
