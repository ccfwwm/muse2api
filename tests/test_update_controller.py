"""Real local Git repositories exercise merges, conflict handling and queue status."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_controller", ROOT / "deploy/update-controller.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(cwd, *args):
    return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="muse2api-controller-")
        self.base = Path(self.temp.name)
        self.upstream = self.base / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-b", "main")
        git(self.upstream, "config", "user.name", "Test")
        git(self.upstream, "config", "user.email", "test@example.com")
        (self.upstream / "app.txt").write_text("base\n")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-m", "base")
        self.origin = self.base / "origin.git"
        git(self.base, "clone", "--bare", str(self.upstream), str(self.origin))
        self.root = self.base / "production"
        (self.root / "state/updates").mkdir(parents=True)
        (self.root / "state/.env").write_text("private-value-do-not-copy\n")
        self.controller = module.UpdateController(self.root)
        self.controller.environment.update(GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
            GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com")
        self.remotes = patch.multiple(module, ORIGIN=str(self.origin), UPSTREAM=str(self.upstream))
        self.remotes.start()

    def tearDown(self):
        self.remotes.stop()
        # Git's object files can be read-only on Windows.
        def repair(function, path, info):
            os.chmod(path, 0o700)
            function(path)
        import shutil
        assert Path(self.temp.name).resolve().parent == Path(tempfile.gettempdir()).resolve()
        shutil.rmtree(self.temp.name, onerror=repair)
        self.temp.cleanup()

    def request(self):
        (self.root / "state/updates/request.json").write_text(json.dumps({"request_id": "a" * 32}))

    def state(self):
        return json.loads((self.root / "state/updates/status.json").read_text(encoding="utf-8"))

    def test_upstream_merge_preserves_deployment_files_and_private_state(self):
        self.controller.request_id = "a" * 32
        self.controller.trigger()
        repo = self.controller.repo
        (repo / "deploy.txt").write_text("shared sub2api network\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "local deployment")
        git(repo, "push", "origin", "HEAD:main")
        (self.upstream / "app.txt").write_text("new upstream feature\n")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-m", "upstream update")
        self.request()
        with patch.object(self.controller, "wait_for_deployment", side_effect=lambda revision: revision):
            self.controller.run()
        self.assertEqual(self.state()["state"], "completed")
        self.assertEqual((repo / "app.txt").read_text(), "new upstream feature\n")
        self.assertEqual((repo / "deploy.txt").read_text(), "shared sub2api network\n")
        self.assertEqual((self.root / "state/.env").read_text(), "private-value-do-not-copy\n")
        self.assertNotIn(".env", git(repo, "ls-files").splitlines())

    def test_conflict_does_not_push_or_claim_success(self):
        self.controller.request_id = "a" * 32
        self.controller.trigger()
        repo = self.controller.repo
        (repo / "app.txt").write_text("local edit\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "local change")
        git(repo, "push", "origin", "HEAD:main")
        before = git(self.origin, "rev-parse", "main")
        (self.upstream / "app.txt").write_text("conflicting upstream edit\n")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-m", "conflict")
        self.request()
        with patch.object(self.controller, "wait_for_deployment") as wait:
            self.controller.run()
        wait.assert_not_called()
        self.assertEqual(git(self.origin, "rev-parse", "main"), before)
        self.assertEqual(self.state()["state"], "failed")
        self.assertIn("合并冲突", self.state()["message"])

    def test_failed_build_is_reported_without_success(self):
        self.request()
        with patch.object(self.controller, "wait_for_deployment", side_effect=RuntimeError("build failed")):
            self.controller.run()
        self.assertEqual(self.state()["state"], "failed")
        self.assertFalse((self.root / "state/updates/request.json").exists())


if __name__ == "__main__":
    unittest.main()
