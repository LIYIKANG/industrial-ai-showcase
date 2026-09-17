#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
image_name=industrial-ai-showcase:1.2.0
image_arch=$(docker image inspect --format '{{.Architecture}}' "$image_name")
package_dir=./Docker交付包
mkdir -p "$package_dir"
archive="$package_dir/industrial-ai-showcase-1.2.0-linux-$image_arch.tar.gz"
temp_archive="$archive.partial"
trap 'rm -f "$temp_archive"' EXIT HUP INT TERM
# Avoid a pipeline hiding docker-save failures: save first, then compress.
raw_archive="$package_dir/image-export.tar"
docker image save -o "$raw_archive" "$image_name"
gzip -c "$raw_archive" > "$temp_archive"
mv "$temp_archive" "$archive"
rm -f "$raw_archive"
cp docker/compose.image.yaml "$package_dir/compose.yaml"
cp docker/env.example "$package_dir/env.example"
printf '已导出：%s\n' "$archive"
