# Fork automation

- **Sync upstream** runs daily at **03:00 UTC**, or manually. It merges
  upstream into `master`, then calls **Build** for the exact resulting commit
  and **Publish ISO** for that build's artifacts. It also builds when upstream
  has no new commits.
- Files listed in `protected-files.txt` retain their canonical fork copies.
  Other merge conflicts abort the sync and open an issue; the remote fork is
  left unchanged. Update canonical and active copies together when changing
  a protected file.
- Pushes to `master` and manual **Build** runs produce a Clang amd64 Release
  ISO. Pull-request artifacts are not published as releases.
- Releases use `daily-YYYY-MM-DD-<commit>` tags, with UTC dates and immutable
  source revisions. Each contains one ISO and its SHA-256 file. Additional
  commits built on the same day have separate dated releases; rerunning the
  same revision on the same day replaces its assets.
- A successful build can publish only while its revision is still the
  current `master`. A later commit supersedes older builds. The release is
  activated as latest only after its assets have been uploaded.
- After the first dated release, the old `latest` release is labeled as a
  legacy archive. Its existing files are retained.
- The update server reads GitHub's latest published release and synchronizes
  hourly at minute 17. Cancelled, failed or blocked builds produce no new
  package; the server continues serving the last successful update.

The existing GitHub execution block must be resolved independently of this
workflow configuration if it prevents runner assignment.

Run the automation regression checks with Python 3.11+ and PyYAML:

```sh
python3 -m unittest discover -s .github/tests -v
```
