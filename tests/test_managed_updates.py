"""Managed images compare upstream provenance and queue authenticated updates."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HOME = tempfile.TemporaryDirectory(prefix="muse2api-update-test-")
os.environ.update(MUSE2API_HOME=HOME.name, MUSE2API_PROFILE_ROOT=HOME.name,
                  MUSE2API_KEY="m2a_test", MUSE2API_UPDATE_MODE="github-actions",
                  MUSE2API_REVISION="b" * 40, MUSE2API_UPSTREAM_SHA="a" * 40)
import app
from test_admin_base_url import Client


class Reply:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


class ManagedUpdates(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory(prefix="muse2api-queue-")
        self.config = patch.object(app.CFG, "base_dir", self.home.name)
        self.config.start()
        app._UPDATE_CACHE.update(ts=0, data=None)

    def tearDown(self):
        self.config.stop()
        self.home.cleanup()

    def check_version(self, sha=None, status=200):
        def get(url, **kwargs):
            if "version.json" in url:
                return Reply({"version": "1.6.9"})
            return Reply([{"sha": sha or "a" * 40, "commit": {
                "message": "upstream change", "committer": {"date": "2026-11-08T00:00:00Z"}}}], status)
        with patch("requests.get", side_effect=get):
            return app._check_update_sync(force=True)

    def test_fork_only_commits_do_not_report_update(self):
        result = self.check_version()
        self.assertEqual(result["local_sha"], "a" * 7)
        self.assertEqual(result["deployment_sha"], "b" * 40)
        self.assertFalse(result["has_update"])
        self.assertTrue(result["up_to_date"])

    def test_new_upstream_commit_reports_update_even_same_version(self):
        self.assertTrue(self.check_version("c" * 40)["has_update"])

    def test_failed_github_check_does_not_claim_current(self):
        result = self.check_version(status=429)
        self.assertFalse(result["up_to_date"])
        self.assertFalse(result["has_update"])

    def test_missing_provenance_does_not_guess_from_version(self):
        with patch.dict(os.environ, MUSE2API_UPSTREAM_SHA=""):
            result = self.check_version()
        self.assertFalse(result["up_to_date"])
        self.assertEqual(result["local_sha"], "")

    def test_authenticated_request_queues_without_native_upgrade(self):
        async def run():
            async with Client(app.app, {}) as anonymous:
                self.assertEqual((await anonymous.request("POST", "/admin/update/upgrade")).status_code, 401)
                self.assertEqual((await anonymous.get("/admin/update/status")).status_code, 401)
            async with Client(app.app, {"Authorization": "Bearer m2a_test"}) as client:
                with patch.object(app, "_upgrade_from_github_sync", side_effect=AssertionError("native upgrade called")):
                    response = await client.request("POST", "/admin/update/upgrade")
                self.assertEqual(response.status_code, 200)
                queued = response.json()
                self.assertTrue(queued["managed"])
                request = json.loads((Path(self.home.name) / "updates/request.json").read_text())
                self.assertEqual(request, {"request_id": queued["request_id"]})
                state = (await client.get("/admin/update/status")).json()
                self.assertEqual(state["state"], "queued")
                self.assertEqual((await client.request("POST", "/admin/update/upgrade")).status_code, 409)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
