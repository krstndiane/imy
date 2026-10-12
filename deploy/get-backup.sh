#!/usr/bin/env bash
# get-backup.sh - bring a backup of the live site down to your Mac, and check it opens.
#
#   bash deploy/get-backup.sh ubuntu@SERVER_IP ~/Downloads/ssh-key.key
#   bash deploy/get-backup.sh ubuntu@SERVER_IP ~/Downloads/ssh-key.key ~/Documents/imy-backups
#
# The server already keeps its own daily backups, but they sit on the same disk as the site: they
# save you from a bad update, not from losing the server. Run this now and then (once a week is
# plenty) so a copy also lives somewhere else - your Mac, and from there iCloud or Google Drive.
#
# It makes a fresh backup on the server, downloads it, and opens it to prove it's complete. It only
# ever reads: nothing on the server is changed or deleted.
set -euo pipefail
HOST=${1:?"Usage: bash deploy/get-backup.sh ubuntu@SERVER_IP path/to/ssh-key.key [folder-to-save-in]"}
KEY=${2:?"Usage: bash deploy/get-backup.sh ubuntu@SERVER_IP path/to/ssh-key.key [folder-to-save-in]"}
DEST=${3:-"$HOME/imy-backups"}
cd "$(dirname "$0")/.."
chmod 600 "$KEY"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
DC='sudo docker compose -f ~/imy/deploy/docker-compose.yml --env-file ~/imy/.env'

echo "Making a fresh backup on the server..."
"${SSH[@]}" "$HOST" "$DC exec -T imy python -m backend.backup" | sed 's/^/  /'

name=$("${SSH[@]}" "$HOST" "$DC exec -T imy sh -c 'ls -t /data/backups/*.zip | head -1'" | tr -d '\r')
[ -n "$name" ] || { echo "No backup found on the server."; exit 1; }

mkdir -p "$DEST"
out="$DEST/$(basename "$name")"
echo "Downloading $(basename "$name") to $DEST ..."
"${SSH[@]}" "$HOST" "$DC exec -T imy cat '$name'" > "$out"
[ -s "$out" ] || { echo "The download came out empty."; rm -f "$out"; exit 1; }

echo "Checking the copy on your Mac opens..."
if python3 -m backend.backup --check "$out" 2>/dev/null; then
  :
elif command -v unzip >/dev/null && unzip -tq "$out" >/dev/null; then
  echo "  The zip is complete. (For the full check, run it from the imy folder with the .venv on:"
  echo "   source .venv/bin/activate && python -m backend.backup --check '$out')"
else
  echo "  WARNING: couldn't check this file. Try opening it by hand before you rely on it."
fi

echo
echo "Saved: $out"
echo "Keep it somewhere that isn't this Mac either - an iCloud or Google Drive folder is enough."
echo "To put it back one day:  python -m backend.backup --restore '$out'   (then follow what it prints)"
