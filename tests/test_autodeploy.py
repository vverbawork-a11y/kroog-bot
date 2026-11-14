"""Deployment trust, isolation and real child-process recovery checks."""
import io
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import autodeploy as deploy

SHA = "a" * 40
OTHER = "b" * 40


def archive(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as output:
        for name, body in entries.items():
            output.writestr(name, body)
    data.seek(0)
    return data


class ArchiveTests(unittest.TestCase):
    def test_unsafe_paths_and_symlinks_are_rejected(self):
        for name in ("repo/../escape.py", "/absolute.py", "repo/a\\b.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    deploy.unpack(archive({name: "bad"}), Path(folder))
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as output:
            item = zipfile.ZipInfo("repo/main.py")
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(item, "/etc/passwd")
        data.seek(0)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            deploy.unpack(data, Path(folder))

    def test_runtime_data_and_credentials_never_come_from_source_archive(self):
        entries = {"repo/"+name: "# fixture" for name in (
            "main.py", "config.py", "database.py", "shop.py", "requirements.txt",
            "miniapp/index.html", "photos/photo.jpg", "templates/template.pdf")}
        entries.update({"repo/"+name: "PRIVATE" for name in (
            "data/kroog.db", "config_local.py", ".env", ".git/config", ".github/workflows/run.yml")})
        with tempfile.TemporaryDirectory() as folder:
            deploy.unpack(archive(entries), Path(folder))
            self.assertTrue((Path(folder)/"miniapp/index.html").exists())
            self.assertTrue((Path(folder)/"main.py").exists())
            for name in ("data", "config_local.py", ".env", ".git", ".github"):
                self.assertFalse((Path(folder)/name).exists(), name)

    def test_partial_and_oversized_archives_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                deploy.unpack(archive({"repo/main.py": "pass"}), Path(folder))
            with patch.object(deploy, "MAX_EXPANDED", 2), self.assertRaises(ValueError):
                deploy.unpack(archive({"repo/main.py": "pass"}), Path(folder))


class TrustTests(unittest.TestCase):
    def run_record(self, **values):
        return dict(status="completed", conclusion="success", event="push",
                    head_branch="main", head_repository={"full_name": deploy.REPOSITORY},
                    head_sha=SHA, run_number=5) | values

    def test_only_our_successful_main_push_can_be_installed(self):
        with patch.object(deploy, "github", return_value={"workflow_runs": [self.run_record()]}):
            self.assertEqual(deploy.candidate(), (SHA, 5))
        for values in ({"conclusion":"failure"}, {"status":"in_progress"},
                       {"event":"pull_request"}, {"head_branch":"feature"},
                       {"head_sha":"../bad"}, {"run_number":"5"},
                       {"head_repository":{"full_name":"someone/fork"}}):
            with self.subTest(values=values), patch.object(deploy, "github", return_value={
                "workflow_runs": [self.run_record(**values), self.run_record()]}):
                self.assertIsNone(deploy.candidate())


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)/"image"
        self.root.mkdir()
        self.write_main(self.root)
        (self.root/"requirements.txt").write_text("")
        self.env = patch.dict(os.environ, {"DB_PATH":"data/kroog.db", "KROOG_PROBATION_SECONDS":"1"})
        self.env.start()
        self.supervisor = deploy.Supervisor(self.root)

    def tearDown(self):
        self.supervisor.stop_child()
        self.env.stop()
        self.tmp.cleanup()

    def write_main(self, path, fail=False):
        path.mkdir(exist_ok=True, parents=True)
        (path/"main.py").write_text('''import os, time
from pathlib import Path
if __name__ == "__main__":
    Path(os.environ["KROOG_READY_FILE"]).write_text("ready")
    while True: time.sleep(.1)
''' if not fail else 'if __name__ == "__main__": raise SystemExit(2)\n')

    def release(self, fail=False):
        path = self.supervisor.releases/SHA
        self.write_main(path, fail)
        return deploy.Release(path, deploy.sys.executable, SHA, 5)

    def test_code_update_preserves_database_and_uploads_and_restarts_once(self):
        self.supervisor.database.parent.mkdir(exist_ok=True)
        with deploy.sqlite3.connect(self.supervisor.database) as db:
            db.execute("CREATE TABLE orders (id INTEGER)")
            db.execute("INSERT INTO orders VALUES (123)")
        uploads = self.supervisor.database.parent/"uploads"
        uploads.mkdir()
        (uploads/"customer.pdf").write_bytes(b"KEEP")
        self.assertTrue(self.supervisor.launch())
        old = self.supervisor.child
        self.assertTrue(self.supervisor.activate(self.release()))
        self.assertIsNotNone(old.poll())
        self.assertIsNone(self.supervisor.child.poll())
        self.assertEqual(self.supervisor.current.sha, SHA)
        self.assertEqual((uploads/"customer.pdf").read_bytes(), b"KEEP")
        with deploy.sqlite3.connect(self.supervisor.database) as db:
            self.assertEqual(db.execute("SELECT id FROM orders").fetchone()[0],123)
        self.assertTrue((self.supervisor.home/"before-update.sqlite3").exists())
        self.assertEqual(self.supervisor.environment()["DB_PATH"], str(self.supervisor.database))

    def test_failed_start_returns_to_previous_bot_and_blocks_bad_commit(self):
        self.assertTrue(self.supervisor.launch())
        self.assertFalse(self.supervisor.activate(self.release(fail=True)))
        self.assertEqual(self.supervisor.current.path,self.root)
        self.assertIsNone(self.supervisor.child.poll())
        self.assertIn(SHA,self.supervisor.state["failed"])
        self.assertEqual(deploy.read_json(self.supervisor.status_file)["phase"],"rolled_back")

    def test_github_download_failure_keeps_current_process_alive(self):
        self.assertTrue(self.supervisor.launch())
        child = self.supervisor.child
        with patch.object(deploy,"candidate",return_value=(SHA,5)), patch.object(
                self.supervisor,"prepare",side_effect=OSError("network down")):
            self.supervisor.check()
        self.assertIs(self.supervisor.child,child)
        self.assertIsNone(child.poll())
        self.assertEqual(deploy.read_json(self.supervisor.status_file)["phase"],"update_failed")

    def test_newer_push_during_preparation_cancels_outdated_deploy(self):
        self.assertTrue(self.supervisor.launch())
        child = self.supervisor.child
        with patch.object(deploy,"candidate",return_value=(SHA,5)), patch.object(
                self.supervisor,"prepare",return_value=self.release()), patch.object(
                deploy,"is_tip",return_value=False):
            self.supervisor.check()
        self.assertIs(self.supervisor.child,child)
        self.assertIsNone(child.poll())
        self.assertFalse((self.supervisor.releases/SHA).exists())

    def test_failed_and_older_versions_are_not_retried(self):
        self.supervisor.state["failed"]=[SHA]
        with patch.object(deploy,"candidate",return_value=(SHA,5)), patch.object(
                self.supervisor,"prepare") as prepare:
            self.supervisor.check()
            prepare.assert_not_called()
        self.supervisor.current.number=6
        with patch.object(deploy,"candidate",return_value=(OTHER,5)), patch.object(
                self.supervisor,"prepare") as prepare:
            self.supervisor.check()
            prepare.assert_not_called()

    def test_restart_reuses_last_healthy_release(self):
        self.supervisor.current=self.release()
        self.supervisor.save()
        restored=deploy.Supervisor(self.root)
        self.assertEqual(restored.current.sha,SHA)
        self.assertEqual(restored.current.path,self.supervisor.current.path)


if __name__ == "__main__":
    unittest.main()
