# PROJECT:     LiberNT fork automation tests
# LICENSE:     GPL-3.0-or-later
# PURPOSE:     Verify synchronization and ISO publication without GitHub side effects
# COPYRIGHT:   Copyright 2026 ZorroDJ <ugo@mydsl.it>

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("publish_iso", ROOT / ".github/scripts/publish_iso.py")
PUBLISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISH)
SHA = "a" * 40


def workflow(name):
    return yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())


def triggers(data):
    return data.get("on", data.get(True))


def iso_bytes():
    return bytes(32769) + b"CD001" + bytes(100)


class WorkflowTests(unittest.TestCase):
    def test_schedule_chains_exact_merged_revision(self):
        sync = workflow("sync-upstream")
        self.assertEqual(triggers(sync)["schedule"], [{"cron": "0 3 * * *"}])
        self.assertEqual(sync["jobs"]["build"]["needs"], "sync")
        self.assertEqual(sync["jobs"]["build"]["with"]["ref"], "${{ needs.sync.outputs.sha }}")
        self.assertEqual(sync["jobs"]["publish"]["needs"], ["sync", "build"])
        self.assertIn("needs.build.outputs.source_sha", sync["jobs"]["publish"]["with"]["source_sha"])

    def test_manual_and_reusable_builds_upload_the_iso(self):
        build = workflow("build")
        self.assertNotIn("schedule", triggers(build))
        self.assertIn("workflow_call", triggers(build))
        steps = build["jobs"]["build-linux"]["steps"]
        upload = next(s for s in steps if s.get("name") == "Upload bootcd.iso")
        self.assertNotIn("if", upload)
        self.assertFalse(upload["with"]["archive"])
        self.assertNotIn("CCACHE_SEED", json.dumps(build))
        self.assertEqual(build["jobs"]["build-linux"]["strategy"]["matrix"]["include"],
                         [{"compiler": "clang", "arch": "amd64", "build": "release"}])

    def test_protected_manifest_is_valid(self):
        pairs = [line.split() for line in (ROOT / ".github/protected-files.txt").read_text().splitlines()]
        self.assertEqual(len({p[1] for p in pairs}), len(pairs))
        for source, destination in pairs:
            self.assertTrue((ROOT / source).is_file(), source)
            self.assertEqual((ROOT / source).read_bytes(), (ROOT / destination).read_bytes(), destination)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.upstream = self.work / "upstream"
        self.fork = self.work / "fork"
        self.origin = self.work / "origin.git"
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        self.git(self.work, "init", "--initial-branch=master", str(self.upstream))
        self.pairs = [line.split() for line in (ROOT / ".github/protected-files.txt").read_text().splitlines()]
        for source, destination in self.pairs:
            self.write(self.upstream / destination, "upstream base\n")
        self.write(self.upstream / "shared.txt", "base\n")
        self.commit(self.upstream, "Base")
        self.git(self.work, "clone", str(self.upstream), str(self.fork))
        for source, destination in self.pairs:
            self.write(self.fork / source, "fork protected\n")
            self.write(self.fork / destination, "fork protected\n")
        self.write(self.fork / ".github/protected-files.txt", (ROOT / ".github/protected-files.txt").read_text())
        self.commit(self.fork, "Fork customizations")
        self.git(self.work, "clone", "--bare", str(self.fork), str(self.origin))
        self.git(self.fork, "remote", "set-url", "origin", str(self.origin))
        self.before = self.git(self.fork, "rev-parse", "HEAD")
        self.output = self.work / "output"
        self.calls = self.work / "gh-calls"
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.write(self.bin / "gh", "#!/usr/bin/env python3\nimport os,sys\n"
                   "with open(os.environ['GH_CALLS'],'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')\n")
        (self.bin / "gh").chmod(0o755)
        self.env.update(UPSTREAM_URL=str(self.upstream), GITHUB_OUTPUT=str(self.output),
                        GITHUB_REPOSITORY="example/fork", GH_CALLS=str(self.calls),
                        PATH=f"{self.bin}{os.pathsep}{self.env['PATH']}")
        steps = workflow("sync-upstream")["jobs"]["sync"]["steps"]
        self.script = next(s["run"] for s in steps if s.get("id") == "merge")

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def git(self, directory, *args):
        return subprocess.check_output(["git", "-C", str(directory), *args], env=self.env,
                                       text=True, stderr=subprocess.PIPE).strip()

    def commit(self, directory, title):
        self.git(directory, "add", ".")
        self.git(directory, "commit", "-m", title)

    def sync(self):
        return subprocess.run(["bash", "-c", self.script], cwd=self.fork, env=self.env,
                              capture_output=True, text=True)

    def assert_synced(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = self.git(self.fork, "rev-parse", "HEAD")
        self.assertEqual(self.git(self.origin, "rev-parse", "master"), after)
        self.assertIn(f"sha={after}", self.output.read_text())
        self.assertEqual(self.git(self.fork, "status", "--porcelain"), "")
        for source, destination in self.pairs:
            self.assertEqual((self.fork / source).read_bytes(), (self.fork / destination).read_bytes())

    def test_clean_merge_and_second_run_without_new_commit(self):
        self.write(self.upstream / "new.txt", "new upstream content\n")
        self.commit(self.upstream, "New content")
        self.assert_synced(self.sync())
        after = self.git(self.fork, "rev-parse", "HEAD")
        self.assertEqual(len(self.git(self.fork, "rev-list", "--parents", "-n", "1", "HEAD").split()), 3)
        self.assertTrue((self.fork / "new.txt").exists())
        self.assert_synced(self.sync())
        self.assertEqual(self.git(self.fork, "rev-parse", "HEAD"), after)

    def test_conflict_in_protected_file_keeps_canonical(self):
        self.write(self.upstream / "README.md", "upstream changed\n")
        self.commit(self.upstream, "Update upstream README")
        self.assert_synced(self.sync())
        self.assertEqual((self.fork / "README.md").read_text(), "fork protected\n")

    def test_real_conflict_aborts_without_pushing(self):
        self.write(self.fork / "shared.txt", "fork change\n")
        self.commit(self.fork, "Fork behavior")
        self.git(self.fork, "push", "origin", "master")
        before = self.git(self.fork, "rev-parse", "HEAD")
        self.write(self.upstream / "shared.txt", "upstream change\n")
        self.commit(self.upstream, "Conflicting behavior")
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.fork, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(self.origin, "rev-parse", "master"), before)
        self.assertEqual(self.git(self.fork, "status", "--porcelain"), "")
        self.assertIn("issue create", self.calls.read_text())
        self.assertFalse(self.output.exists())

    def test_concurrent_push_is_not_overwritten(self):
        other = self.work / "other"
        self.git(self.work, "clone", str(self.origin), str(other))
        self.write(other / "other.txt", "another fork change\n")
        self.commit(other, "Concurrent update")
        self.git(other, "push", "origin", "master")
        remote = self.git(self.origin, "rev-parse", "master")
        self.write(self.upstream / "new.txt", "upstream\n")
        self.commit(self.upstream, "Upstream update")
        self.assertNotEqual(self.sync().returncode, 0)
        self.assertEqual(self.git(self.origin, "rev-parse", "master"), remote)
        self.assertFalse(self.output.exists())


class PublishTests(unittest.TestCase):
    def run_data(self, **values):
        return {"id": 42, "head_repository": {"full_name": "example/fork"},
                "head_branch": "master", "head_sha": SHA, "name": "Build", "event": "push",
                "conclusion": "success", "html_url": "https://github.com/example/fork/actions/runs/42", **values}

    def test_accept_success_and_reject_failed_foreign_or_pr_builds(self):
        self.assertEqual(PUBLISH.source_revision(self.run_data(), "example/fork", "", "99"), SHA)
        for changes in ({"conclusion": "cancelled"}, {"conclusion": "failure"},
                        {"head_branch": "feature"}, {"event": "pull_request"},
                        {"head_repository": {"full_name": "someone/else"}}, {"head_sha": "invalid"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PUBLISH.source_revision(self.run_data(**changes), "example/fork", "", "99")

    def test_chained_sync_uses_post_merge_sha(self):
        run = self.run_data(name="Sync upstream", event="schedule", conclusion=None, head_sha="b" * 40)
        self.assertEqual(PUBLISH.source_revision(run, "example/fork", SHA, "42"), SHA)
        with self.assertRaises(ValueError):
            PUBLISH.source_revision(run, "example/fork", SHA, "99")

    def test_select_exact_unexpired_artifact(self):
        artifact = {"id": 7, "name": f"bootcd-clang-amd64-release-{SHA[:11]}.iso", "expired": False}
        self.assertEqual(PUBLISH.select_artifact([artifact], SHA), artifact)
        for items in ([], [artifact, artifact], [{**artifact, "expired": True}],
                      [{**artifact, "name": "bootcd-other.iso"}]):
            with self.assertRaises(ValueError):
                PUBLISH.select_artifact(items, SHA)

    def test_release_name_includes_day_and_immutable_revision(self):
        self.assertEqual(PUBLISH.release_names("2026-11-23", SHA),
                         ("daily-2026-11-23-aaaaaaaaaaa", "LiberNT-amd64-release-2026-11-23-aaaaaaaaaaa.iso"))
        self.assertNotEqual(PUBLISH.release_names("2026-11-23", SHA),
                            PUBLISH.release_names("2026-11-23", "b" * 40))

    def test_raw_and_archived_iso_and_invalid_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for archived in (False, True):
                package, target = root / "package", root / "image.iso"
                target.unlink(missing_ok=True)
                outside = root.parent / f"{root.name}-outside.iso"
                if archived:
                    with zipfile.ZipFile(package, "w") as archive:
                        archive.writestr(f"../{outside.name}", iso_bytes())
                else:
                    package.write_bytes(iso_bytes())
                PUBLISH.unpack_iso(package, target)
                self.assertEqual(target.read_bytes(), iso_bytes())
                self.assertFalse(outside.exists())
            package.write_bytes(b"not an ISO")
            with self.assertRaises(ValueError):
                PUBLISH.unpack_iso(package, target)
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("one.iso", iso_bytes())
                archive.writestr("two.iso", iso_bytes())
            with self.assertRaises(ValueError):
                PUBLISH.unpack_iso(package, target)

    def test_publication_uploads_before_activating_release(self):
        calls = []
        artifact = {"id": 7, "name": f"bootcd-clang-amd64-release-{SHA[:11]}.iso"}

        def api(path):
            return {"artifacts": [artifact]} if path.endswith("/artifacts") else self.run_data()

        def gh(*args, **kwargs):
            calls.append(args)
            if args[0] == "api":
                kwargs["stdout"].write(iso_bytes())
            if args[:2] == ("release", "upload"):
                self.assertEqual(Path(args[3]).read_bytes(), iso_bytes())
                self.assertIn(Path(args[3]).name, Path(args[4]).read_text())

        env = {"GITHUB_REPOSITORY": "example/fork", "BUILD_RUN_ID": "42", "SOURCE_SHA": "",
               "GITHUB_RUN_ID": "99", "GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}
        with patch.dict(os.environ, env), patch.object(PUBLISH, "api", side_effect=api), \
                patch.object(PUBLISH, "current_revision", return_value=SHA), \
                patch.object(PUBLISH, "optional_release", return_value=None), \
                patch.object(PUBLISH, "gh", side_effect=gh):
            PUBLISH.main()
        commands = [args[1] for args in calls if args[0] == "release"]
        self.assertEqual(commands, ["create", "upload", "edit"])
        self.assertIn("--draft", calls[1])
        self.assertIn("--latest", calls[3])
        self.assertTrue(all(args[2] != "latest" for args in calls if args[0] == "release"))

    def test_superseded_build_is_not_downloaded_or_published(self):
        env = {"GITHUB_REPOSITORY": "example/fork", "BUILD_RUN_ID": "42", "SOURCE_SHA": "",
               "GITHUB_RUN_ID": "99"}
        with patch.dict(os.environ, env), patch.object(PUBLISH, "api", return_value=self.run_data()), \
                patch.object(PUBLISH, "current_revision", return_value="b" * 40), \
                patch.object(PUBLISH, "gh") as gh:
            PUBLISH.main()
            gh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
