#!/usr/bin/env python3
"""Host-side update queue. No Docker socket or GitHub credential enters the app."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ORIGIN = "git@github.com:ccfwwm/muse2api.git"
UPSTREAM = "https://github.com/czg86389-hub/muse2api.git"


class UpdateController:
    def __init__(self, root=Path("/opt/muse2api")):
        self.root = Path(root)
        self.work = self.root / "updater"
        self.repo = self.work / "repo"
        self.queue = self.root / "state/updates"
        self.request_id = ""
        self.environment = dict(os.environ, GIT_TERMINAL_PROMPT="0",
            GIT_SSH_COMMAND=f"ssh -i {self.work}/github_ed25519 -o IdentitiesOnly=yes "
                            f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={self.work}/known_hosts")

    def status(self, state, message, **extra):
        value = {"request_id": self.request_id, "state": state,
                 "message": message, "updated_at": time.time(), **extra}
        temporary = self.queue / "status.json.next"
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.queue / "status.json")

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd or self.repo,
            env=self.environment, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Commands contain only fixed paths and public URLs; never log key contents.
            raise RuntimeError(result.stderr.strip()[-1200:] or "Git command failed")
        return result.stdout.strip()

    def trigger(self):
        self.work.mkdir(mode=0o700, exist_ok=True)
        if not (self.repo / ".git").exists():
            self.git("clone", ORIGIN, str(self.repo), cwd=self.work)
        self.git("fetch", "origin", "main")
        # This is a dedicated disposable checkout, never the production state directory.
        self.git("checkout", "-B", "online-update", "origin/main")
        self.git("config", "user.name", "muse2api-updater")
        self.git("config", "user.email", "muse2api-updater@users.noreply.github.com")
        if "upstream" not in self.git("remote").splitlines():
            self.git("remote", "add", "upstream", UPSTREAM)
        self.git("fetch", "upstream", "main")
        try:
            self.git("merge", "--no-edit", "upstream/main")
        except RuntimeError:
            subprocess.run(["git", "merge", "--abort"], cwd=self.repo,
                env=self.environment, capture_output=True, timeout=30)
            raise RuntimeError("上游更新与本地配置存在合并冲突，当前运行版本未更改，请人工处理")
        # A repository-specific deploy key push triggers the existing build workflow.
        # An empty commit also permits a manual retry when upstream is unchanged.
        self.git("commit", "--allow-empty", "-m", "chore: request online update " + self.request_id)
        revision = self.git("rev-parse", "HEAD")
        self.git("push", "origin", "HEAD:main")
        return revision

    def wait_for_deployment(self, revision):
        deadline = time.monotonic() + 1500
        next_check = 0
        workflow_url = "https://github.com/ccfwwm/muse2api/actions"
        while time.monotonic() < deadline:
            try:
                deployed = json.loads((self.root / "deployment.json").read_text())["revision"]
                if deployed == revision:
                    return deployed
                # The workflow may have merged a newer upstream commit after our push.
                if re.fullmatch(r"[0-9a-f]{40}", deployed) and time.monotonic() >= next_check:
                    self.git("fetch", "origin", "main")
                    result = subprocess.run(["git", "merge-base", "--is-ancestor", revision, deployed],
                        cwd=self.repo, env=self.environment, capture_output=True, timeout=20)
                    if result.returncode == 0:
                        return deployed
            except (OSError, ValueError, KeyError, RuntimeError):
                pass
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 45
                url = f"https://api.github.com/repos/ccfwwm/muse2api/actions/runs?head_sha={revision}&event=push&per_page=5"
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": "muse2api-updater"})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        runs = json.load(response).get("workflow_runs", [])
                    runs = [r for r in runs if r.get("path") == ".github/workflows/deploy.yml"]
                    if runs:
                        run = runs[0]
                        workflow_url = run["html_url"]
                        if run["status"] == "completed" and run.get("conclusion") != "success":
                            raise RuntimeError("GitHub 构建或部署失败，当前版本已保留或回退；请查看更新记录")
                        self.status("building", "正在构建并部署，完成后页面会自动刷新",
                                    revision=revision, workflow_url=workflow_url)
                except (OSError, ValueError, KeyError):
                    pass
            time.sleep(5)
        raise RuntimeError("等待自动部署超时，请查看 GitHub 更新记录；可稍后重试")

    def run(self):
        request = self.queue / "request.json"
        if not request.exists():
            return
        processing = self.queue / "processing.json"
        request.replace(processing)
        try:
            value = json.loads(processing.read_text())
            self.request_id = value.get("request_id", "")
            if not re.fullmatch(r"[0-9a-f]{32}", self.request_id):
                raise ValueError("Invalid update request")
            self.status("syncing", "正在检查并合并上游更新")
            revision = self.trigger()
            self.status("building", "已提交更新，正在等待构建和部署", revision=revision,
                        workflow_url="https://github.com/ccfwwm/muse2api/actions")
            deployed = self.wait_for_deployment(revision)
            self.status("completed", "更新已完成，服务健康检查通过", revision=deployed)
        except Exception as error:
            self.status("failed", str(error), workflow_url="https://github.com/ccfwwm/muse2api/actions")
        finally:
            processing.unlink(missing_ok=True)


if __name__ == "__main__":
    os.umask(0o077)
    UpdateController().run()
