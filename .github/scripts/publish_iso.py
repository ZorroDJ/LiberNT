# PROJECT:     LiberNT fork automation
# LICENSE:     GPL-3.0-or-later
# PURPOSE:     Publish a successful master ISO in a dated, revision-specific release
# COPYRIGHT:   Copyright 2026 ZorroDJ <ugo@mydsl.it>

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def gh(*args, **kwargs):
    return subprocess.run(["gh", *args], check=True, **kwargs)


def api(path):
    return json.loads(gh("api", path, capture_output=True, text=True).stdout)


def source_revision(run, repository, requested_sha, current_run_id):
    if (run.get("head_repository") or {}).get("full_name", "").lower() != repository.lower():
        raise ValueError("The build must belong to this repository")
    if run.get("head_branch") != "master":
        raise ValueError("Only master builds can be published")
    if requested_sha:
        if (str(run["id"]) != current_run_id or run.get("name") != "Sync upstream"
                or run.get("event") not in ("schedule", "workflow_dispatch")):
            raise ValueError("A chained build must belong to the current sync run")
        revision = requested_sha
    else:
        if (run.get("conclusion") != "success" or run.get("name") != "Build"
                or run.get("event") not in ("push", "workflow_dispatch")):
            raise ValueError("A completed successful master build is required")
        revision = run["head_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid source revision")
    return revision


def select_artifact(artifacts, revision):
    name = f"bootcd-clang-amd64-release-{revision[:11]}.iso"
    matches = [a for a in artifacts if a["name"] == name and not a.get("expired")]
    if len(matches) != 1:
        raise ValueError(f"Expected one unexpired artifact named {name}")
    return matches[0]


def release_names(day, revision):
    suffix = f"{day}-{revision[:11]}"
    return f"daily-{suffix}", f"LiberNT-amd64-release-{suffix}.iso"


def unpack_iso(package, destination):
    if zipfile.is_zipfile(package):
        with zipfile.ZipFile(package) as archive:
            images = [i for i in archive.infolist() if not i.is_dir() and i.filename.endswith(".iso")]
            if len(images) != 1:
                raise ValueError("The artifact archive must contain exactly one ISO")
            with archive.open(images[0]) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    else:
        package.replace(destination)
    with destination.open("rb") as image:
        image.seek(32769)
        if image.read(5) != b"CD001":
            raise ValueError("Downloaded artifact is not an ISO9660 image")


def optional_release(repository, tag):
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
        capture_output=True, text=True,
    )
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def current_revision(repository):
    return api(f"repos/{repository}/git/ref/heads/master")["object"]["sha"]


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["BUILD_RUN_ID"]
    if not run_id.isdigit():
        raise ValueError("Invalid build run id")
    run = api(f"repos/{repository}/actions/runs/{run_id}")
    revision = source_revision(
        run, repository, os.environ.get("SOURCE_SHA", ""), os.environ["GITHUB_RUN_ID"],
    )
    if revision != current_revision(repository):
        print(f"Skipping superseded build {revision}")
        return
    artifact = select_artifact(
        api(f"repos/{repository}/actions/runs/{run_id}/artifacts")["artifacts"], revision,
    )
    day = datetime.now(timezone.utc).date().isoformat()
    tag, image_name = release_names(day, revision)
    title = f"LiberNT {day} — amd64 Release ({revision[:11]})"
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        package = work / "artifact"
        with package.open("wb") as target:
            gh("api", f"repos/{repository}/actions/artifacts/{artifact['id']}/zip", stdout=target)
        image = work / image_name
        unpack_iso(package, image)
        with image.open("rb") as data:
            digest = hashlib.file_digest(data, "sha256").hexdigest()
        checksum = work / f"{image_name}.sha256"
        checksum.write_text(f"{digest}  {image_name}\n")
        notes = work / "notes.md"
        notes.write_text(
            f"AMD64 Release ISO with DarkMode and the fork customizations.\n\n"
            f"UTC date: {day}\n\n"
            f"Source: [`{revision}`](https://github.com/{repository}/commit/{revision})\n\n"
            f"Build: {run['html_url']}\n\nSHA-256: `{digest}`\n"
        )
        if revision != current_revision(repository):
            print(f"Skipping build superseded during artifact download: {revision}")
            return
        release = optional_release(repository, tag)
        if release is None:
            gh("release", "create", tag, "--repo", repository, "--target", revision,
               "--title", title, "--notes-file", str(notes), "--draft")
        elif release["target_commitish"] != revision:
            raise ValueError("Existing release has a different source revision")
        gh("release", "upload", tag, str(image), str(checksum), "--repo", repository, "--clobber")
        gh("release", "edit", tag, "--repo", repository, "--title", title,
           "--notes-file", str(notes), "--draft=false", "--prerelease=false", "--latest")
    legacy = optional_release(repository, "latest")
    if legacy is not None and legacy["name"] != "Legacy ISO archive (before dated releases)":
        gh("release", "edit", "latest", "--repo", repository,
           "--title", "Legacy ISO archive (before dated releases)", "--latest=false")
    url = f"https://github.com/{repository}/releases/tag/{tag}"
    print(f"Published {url}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"release_url={url}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"## ISO release\n\n[{tag}]({url})\n\nSource: `{revision}`\n")


if __name__ == "__main__":
    main()
