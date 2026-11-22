#!/usr/bin/env bash
# Mirror a LiberNT update, overlay the DarkMode binaries and publish it on our
# own update channel. Intended to run from cron/systemd on the update host.
set -euo pipefail

UPSTREAM=${UPSTREAM:-https://libernt.com/api/update/}
ARCH=${ARCH:-amd64}
BASE_URL=${BASE_URL:-https://liberntupdate.exe2bin.com/}
WEBROOT=${WEBROOT:-/srv/liberntupdate}
PATCH_DIR=${PATCH_DIR:-}
LOCAL_BUILD=${LOCAL_BUILD:-0}
MIN_BUILD=${MIN_BUILD:-$(( $(date +%s) - 1700000000 ))}
OFFSET=${OFFSET:-400000}

[ -n "$PATCH_DIR" ] || { echo "PATCH_DIR is required" >&2; exit 1; }
for f in uxtheme.dll win32k.sys user32.dll win32u.dll; do
    [ -f "$PATCH_DIR/$f" ] || { echo "missing $PATCH_DIR/$f" >&2; exit 1; }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

json=$(curl -fsSL --retry 3 "$UPSTREAM?arch=$ARCH&build=$LOCAL_BUILD")
[ "$(jq -r '.available // false' <<<"$json")" = true ] || { echo "No upstream update."; exit 0; }

url=$(jq -r '.url' <<<"$json")
sha=$(jq -r '.sha256' <<<"$json")
notes=$(jq -r '.notes // ""' <<<"$json")
[ -n "$url" ] && [ -n "$sha" ] || { echo "Malformed upstream manifest" >&2; exit 1; }

curl -fL --retry 3 -o "$work/upstream.zip" "$url"
echo "$sha  $work/upstream.zip" | sha256sum -c -

mkdir "$work/tree"
unzip -q "$work/upstream.zip" -d "$work/tree"
source_iso=$(find "$work/tree" -type f -name '*.iso' | head -n 1)
[ -n "$source_iso" ] || { echo "No ISO in upstream zip" >&2; exit 1; }

mkdir "$work/iso"
xorriso -osirrox on -indev "$source_iso" -extract / "$work/iso" >/dev/null 2>&1
rm -rf "$work/iso/[BOOT]"

for f in uxtheme.dll win32k.sys user32.dll win32u.dll; do
    cp "$PATCH_DIR/$f" "$work/iso/Windows/system32/$f"
done

rm -f "$work/bootcd.iso"
xorriso -as mkisofs -o "$work/bootcd.iso" -V ReactOS -A 'LiberNT Project' \
    -publisher 'LiberNT Project' -iso-level 4 -relaxed-filenames -allow-lowercase -N \
    -G --interval:local_fs:0s-15s:zero_mbrpt,zero_gpt:"$source_iso" \
    -efi-boot-part --efi-boot-image --boot-catalog-hide \
    -b '/loader/isoboot.bin' -no-emul-boot -boot-load-size 4 \
    -eltorito-alt-boot -e '/loader/efisys.bin' -no-emul-boot \
    -boot-load-size 16384 "$work/iso" >/dev/null

# Keep the GPT protective entry active so firmware sees the hybrid image.
printf '\x80\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00' |
    dd of="$work/bootcd.iso" bs=1 seek=462 conv=notrunc status=none

(cd "$work" && zip -0 -q update.zip bootcd.iso)
zip_sha=$(sha256sum "$work/update.zip" | awk '{print $1}')
zip_size=$(stat -c %s "$work/update.zip")
build=$(( OFFSET + $(date +%s) - 1700000000 ))
[ "$build" -ge "$MIN_BUILD" ] || build=$MIN_BUILD

jq -n \
    --argjson tag "$build" \
    --argjson build "$build" \
    --argjson size "$zip_size" \
    --arg name "darkmode-$build" \
    --arg url "${BASE_URL}update.zip" \
    --arg sha "$zip_sha" \
    --arg notes "$notes" \
    '{available:true, tag_id:$tag, build:$build, size:$size, tag_name:$name,
      url:$url, sha256:$sha, notes:$notes}' > "$work/lntupdate.json"

mkdir -p "$WEBROOT"
mv "$work/update.zip" "$WEBROOT/update.zip.new"
mv "$work/lntupdate.json" "$WEBROOT/lntupdate.json.new"
mv "$WEBROOT/update.zip.new" "$WEBROOT/update.zip"
mv "$WEBROOT/lntupdate.json.new" "$WEBROOT/lntupdate.json"
echo "Published build $build ($zip_size bytes, sha256 $zip_sha)."
