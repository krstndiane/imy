#!/usr/bin/env bash
# server-setup.sh - set up (or update) imy on an Ubuntu server, e.g. Oracle Cloud's Always Free one.
#
# You don't run this by hand: deploy/push-to-server.sh (on your Mac) sends the code and runs it.
# Running it again is safe - that's also how updates work. It:
#   1. installs Docker (first time only)
#   2. opens ports 80 and 443 in the server's own firewall (Oracle's Ubuntu blocks them)
#   3. asks for the settings that can't be in git - the address and the Groq, Turso, Supabase and
#      Resend keys - and keeps them in ~/imy/.env, readable only by you (first time only)
#   4. builds imy and starts it with Caddy in front for https
#   5. tries each service, then runs the launch checks against the live address
set -euo pipefail
cd "$(dirname "$0")/.."
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
DC="sudo docker compose -f deploy/docker-compose.yml --env-file .env"

# ---------------------------------------------------------------- 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (first time only, a few minutes)"
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl
  curl -fsSL https://get.docker.com | sudo sh
fi

# ---------------------------------------------------------------- 2. the server's own firewall
# Oracle has two firewalls: the "security list" in the website (you open 80/443 there) and this one,
# inside Ubuntu, which by default only lets SSH in.
say "Opening ports 80 and 443 on the server"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
done
sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 1 -p udp --dport 443 -j ACCEPT
if ! command -v netfilter-persistent >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null
fi
sudo netfilter-persistent save >/dev/null 2>&1 || true      # keep the rules after a reboot

# ---------------------------------------------------------------- 3. settings (first time only)
if [ ! -f .env ]; then
  say "imy's settings (asked once, kept in ~/imy/.env)"
  read -rp "The site's address, without https:// [imy.pdyntech.com]: " domain
  domain=${domain:-imy.pdyntech.com}
  domain=${domain#https://}; domain=${domain#http://}; domain=${domain%%/*}
  echo "Who can open /admin? Their email addresses, comma-separated (kept only on this server)."
  read -rp "Admin emails [pdyntechnology@gmail.com]: " admins
  admins=${admins// /}; admins=${admins:-pdyntechnology@gmail.com}
  echo "Typing is hidden for the keys below. Press Enter to skip one and add it to ~/imy/.env later."
  echo "Groq API key (console.groq.com/keys) - needed to read screenshots and write replies:"
  read -rsp "> " groq; echo
  echo "Turso database URL (turso db show <name> --url, starts with libsql://):"
  read -rp "> " turso_url
  echo "Turso token (turso db tokens create <name>):"
  read -rsp "> " turso_token; echo
  echo "Supabase Project URL (Project Settings > API, https://....supabase.co):"
  read -rp "> " supabase_url
  echo "Supabase service_role key (same page; secret):"
  read -rsp "> " supabase_key; echo
  echo "Resend API key (resend.com > API Keys; pdyntech.com must be verified there first):"
  read -rsp "> " resend; echo
  umask 077
  cat > .env <<ENV
# imy's live settings. Readable only by this user. Never copy this file into git.
IMY_DOMAIN=$domain
IMY_PUBLIC_URL=https://$domain
IMY_CONTACT_EMAIL=pdyntechnology@gmail.com
# Who can open /admin. Not shown anywhere public. Feedback is emailed to IMY_CONTACT_EMAIL.
IMY_ADMIN_EMAILS=$admins

# Email, through Resend, from pdyntech.com
RESEND_API_KEY=$resend
IMY_MAIL_FROM="imy <no-reply@pdyntech.com>"

# The AI (Groq, through Pydantic AI)
GROQ_API_KEY=$groq

# The database (Turso) and pictures (Supabase): the server itself keeps nothing
IMY_TURSO_URL=$turso_url
IMY_TURSO_TOKEN=$turso_token
SUPABASE_URL=$supabase_url
SUPABASE_SERVICE_KEY=$supabase_key

TZ=Asia/Manila
IMY_BACKUP_DAILY=1
ENV
  chmod 600 .env
  echo "Saved. To change a setting later: nano ~/imy/.env, then run this script again."
fi
set -a; . ./.env; set +a

# The certificate only works if the address already points at this server.
here=$(curl -fsS -4 --max-time 10 https://api.ipify.org || true)
there=$(getent ahostsv4 "$IMY_DOMAIN" | awk 'NR==1{print $1}' || true)
if [ -n "$here" ] && [ "$here" != "$there" ]; then
  say "Heads up: $IMY_DOMAIN points to '${there:-nothing}', but this server is $here"
  echo "Point $IMY_DOMAIN at $here (an A record in Route 53), wait a minute, then run this script again."
  echo "(Starting anyway; https will start working by itself once the address points here.)"
fi

# ---------------------------------------------------------------- 4. build and start
say "Building and starting imy (the first build takes a few minutes)"
$DC up -d --build --remove-orphans
sudo docker image prune -f >/dev/null 2>&1 || true            # old builds would slowly fill the disk

say "Waiting for imy to answer"
for i in $(seq 1 60); do
  if $DC exec -T imy python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" >/dev/null 2>&1; then
    echo "imy is up."; break
  fi
  [ "$i" = 60 ] && { echo "imy didn't start. Its last messages:"; $DC logs --tail 40 imy; exit 1; }
  sleep 3
done

# ---------------------------------------------------------------- 5. check it from outside
say "Trying each service with the keys above"
$DC exec -T imy python -m backend.services || true
say "Launch checks"
sleep 5                                                      # give Caddy a moment to get the certificate
$DC exec -T imy python -m backend.preflight --url "https://$IMY_DOMAIN" || true

cat <<DONE

imy is running at https://$IMY_DOMAIN
  See what it's doing:  sudo docker compose -f ~/imy/deploy/docker-compose.yml logs -f imy
  Restart it:           sudo docker compose -f ~/imy/deploy/docker-compose.yml restart imy
  Update it:            from your Mac, run deploy/push-to-server.sh again
DONE
