# DarkMode update mirror

`mirror-update.sh` takes the official LiberNT update, restartes it with the
patched `uxtheme.dll`, `win32k.sys`, `user32.dll` and `win32u.dll`, then
publishes an update compatible with `lntupdate.exe`.

The fork defaults to `https://liberntupdate.exe2bin.com/`; an installed fork
build therefore stays on the DarkMode channel instead of accepting stock
updates that would replace the patched binaries.

## Build a patch directory

Extract the four release files from the fork build:

```bash
PATCH_DIR=/srv/patch
mkdir -p "$PATCH_DIR"
cp output-Clang-amd64-release/dll/win32/uxtheme/uxtheme.dll "$PATCH_DIR/"
cp output-Clang-amd64-release/subsystems/win/win32k/win32k.sys "$PATCH_DIR/"
cp output-Clang-amd64-release/subsystems/win/win32k/user/user32/user32.dll "$PATCH_DIR/"
cp output-Clang-amd64-release/subsystems/win/win32k/win32u/win32u.dll "$PATCH_DIR/"
```

## Run

```bash
sudo env PATCH_DIR=/srv/patch WEBROOT=/srv/liberntupdate \
  BASE_URL=https://liberntupdate.exe2bin.com/ \
  ./mirror-update.sh
```

`LOCAL_BUILD` is the installed build used in the upstream query. `build` in
our manifest is monotonic (`OFFSET` + seconds since 2023), so a mirror build is
always newer than the installed one.

## nginx

```nginx
server {
    listen 443 ssl http2;
    server_name liberntupdate.exe2bin.com;

    root /srv/liberntupdate;
    autoindex off;

    location = / {
        add_header Cache-Control "no-store";
        try_files /lntupdate.json =404;
    }

    location = /update.zip {
        add_header Cache-Control "public, max-age=3600";
        try_files $uri =404;
    }
}
```

The updater appends `?arch=amd64&build=...`, which is ignored by the static
manifest. HTTPS is mandatory: the agent verifies the SHA-256 from the manifest,
but the manifest itself is only protected by TLS.

## systemd timer

```ini
# /etc/systemd/system/libernt-update-mirror.service
[Service]
Type=oneshot
User=libernt
Environment=PATCH_DIR=/srv/patch
Environment=WEBROOT=/srv/liberntupdate
Environment=BASE_URL=https://liberntupdate.exe2bin.com/
Environment=LOCAL_BUILD=0
ExecStart=/srv/libernt/tools/update-mirror/mirror-update.sh
```

```ini
# /etc/systemd/system/libernt-update-mirror.timer
[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```
