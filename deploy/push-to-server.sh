#!/usr/bin/env bash
# push-to-server.sh - put this copy of imy on your server and start it. Run it on your Mac, from the
# imy folder, the first time and every time you want to update the live site:
#
#   bash deploy/push-to-server.sh ubuntu@SERVER_IP ~/Downloads/ssh-key.key
#
# It sends only the files git keeps track of, plus new files git isn't told to ignore. Your .env,
# your database, your chats (data/) and .venv are never sent. The server keeps its own .env and its
# own database, and an update never touches them.
set -euo pipefail
HOST=${1:?"Usage: bash deploy/push-to-server.sh ubuntu@SERVER_IP path/to/ssh-key.key"}
KEY=${2:?"Usage: bash deploy/push-to-server.sh ubuntu@SERVER_IP path/to/ssh-key.key"}
cd "$(dirname "$0")/.."
chmod 600 "$KEY"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)

files=()
while IFS= read -r -d '' f; do
  case "$f" in _to_delete/*) continue;; esac  # old builds set aside for deleting
  [ -e "$f" ] && files+=("$f")               # skip files deleted here but still in git's list
done < <(git ls-files -z --cached --others --exclude-standard)
for f in "${files[@]}"; do
  case "$f" in .env|data/*) echo "Refusing to send $f"; exit 1;; esac   # belt and braces
done

echo "Sending ${#files[@]} files to $HOST (not your .env, database or chats)..."
COPYFILE_DISABLE=1 tar --no-mac-metadata -czf - "${files[@]}" 2>/dev/null \
  | "${SSH[@]}" "$HOST" 'mkdir -p ~/imy && tar -xzf - -C ~/imy --warning=no-unknown-keyword'

echo "Setting up and starting imy on the server..."
"${SSH[@]}" -t "$HOST" 'bash ~/imy/deploy/server-setup.sh'
