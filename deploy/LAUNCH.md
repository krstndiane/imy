# Launch day

Everything in here is a thing only you can do — an account to make, a key to paste, a page to look
at on a real phone. Work down it in order and tick as you go. Nothing here needs code changes.

The code side is done: the site, the admin page at `/admin`, feedback, backups, and the launch
checks (`python -m backend.preflight`).

---

## 1. Before you deploy (about 30 minutes, on your Mac)

Each key goes in `.env` on your Mac. `.env.example` says where each one comes from.

### 1.1 Groq (the AI)  ☐
Required: it reads screenshots and writes the replies.
1. **console.groq.com/keys** → Create API Key → `GROQ_API_KEY=gsk_...`
2. Settings → Data Controls → turn on **Zero Data Retention**.

### 1.2 Turso (the database)  ☐
1. **turso.tech** → sign up → create a database in **Tokyo** or **Singapore**.
2. `turso db show <name> --url` → `IMY_TURSO_URL=libsql://...`
3. `turso db tokens create <name>` → `IMY_TURSO_TOKEN=...`

### 1.3 Supabase (the pictures)  ☐
1. **supabase.com** → New project (same region as Turso).
2. Project Settings → API → Project URL → `SUPABASE_URL`, **service_role** key → `SUPABASE_SERVICE_KEY`.
   Never put the service_role key anywhere but `.env`.

### 1.4 Resend (email from pdyntech.com)  ☐
1. **resend.com** → Domains → Add Domain → `pdyntech.com`.
2. Add every DNS record it shows in **Route 53** → Hosted zones → pdyntech.com → Create record.
   Wait until Resend says **Verified** (minutes, sometimes an hour).
3. API Keys → Create (Sending access) → `RESEND_API_KEY=re_...`

### 1.5 Try them all  ☐
```bash
pip install -r backend/requirements.txt      # installs Pydantic AI
python -m backend.services pdyntechnology@gmail.com
pytest backend/tests -q
```
Every service line should say OK, and the test email should arrive (check spam the first time).
Then `python -m backend.preflight`: everything OK except the lines about the live address.

---

## 2. Deploy

The steps are in the main README under **Deploying**. In short:

1. Oracle Cloud account (Always Free), Ubuntu server, ports 80 and 443 open in the security list.
2. Route 53 → pdyntech.com → Create record: `imy`, type **A**, the server's IP, TTL 300.
3. From your Mac: `bash deploy/push-to-server.sh ubuntu@SERVER_IP ~/Downloads/your-key.key`
   It asks for the address (`imy.pdyntech.com`), the admins, and the Groq, Turso, Supabase and Resend keys.
4. `python -m backend.preflight --url https://imy.pdyntech.com`: every line should say OK.

**Pick the smaller shape.** Oracle may reclaim an Always Free server that looks idle: over 7 days,
CPU under 20%, network under 20% *and* memory under 20%, all three at once. With the database and
pictures elsewhere imy needs very little, so a 1 OCPU / 6 GB server is plenty. Upgrading the account
to Pay As You Go (still ₱0 inside the free limits) stops the reclaiming altogether.

---

## 3. The first hour it's live

### 3.1 Prove the admin page is yours  ☐
Open `https://imy.pdyntech.com/admin`, type **pdyntechnology@gmail.com**, and use
**"No password? Email me a one-time link."** Open the link from your inbox. After that your normal
imy password opens `/admin` by itself, on any device.

### 3.2 Test it on a real phone  ☐
Not the simulator — your own iPhone, on mobile data, not wifi. Make a throwaway account and go all
the way through:

- [ ] Sign up, and tick the age and Terms box
- [ ] Add a person, answer the questions
- [ ] Upload 3–4 real screenshots and check it read them properly
- [ ] Send a few messages and see whether the replies sound right
- [ ] Share a photo in the chat
- [ ] Send feedback (then look for it at `/admin` → Feedback, and in the pdyn inbox)
- [ ] Sign out, use **Forgot password?**, set a new password from the emailed link
- [ ] Delete the account — then check at `/admin` → Accounts that it's gone

If anything is slow or ugly on the phone, tell me and I'll fix it before you share the link.

### 3.3 Check the link preview  ☐
Paste `https://imy.pdyntech.com` into **developers.facebook.com/tools/debug/**, press *Scrape Again*,
and you should see the "Miss mo na?" card. Messenger and Facebook cache the old answer, so always
scrape once after a change. Then send the link to yourself on Messenger and look at the card.

### 3.4 Watch for it going down  ☐
Make a free account at **uptimerobot.com**, add an HTTPS monitor for
`https://imy.pdyntech.com/api/health`, every 5 minutes, with email alerts to pdyntechnology@gmail.com.
It emails you when the site stops answering, so you hear it from a robot and not from a user.

---

## 4. Paperwork, when you have a quiet hour

### 4.1 Data Privacy Act  ☐
imy stores people's private conversations, which the law treats as sensitive personal information,
so this is worth getting right rather than guessing.

Under NPC Circular 2022-04, registering your systems with the National Privacy Commission is
**mandatory** if any of these is true: 250 or more employees; sensitive personal information of
1,000 or more people; the processing is likely to pose a risk to people's rights and freedoms; or
you do automated decision-making or profiling, *whatever* your size. Below those lines, registration
is voluntary, but you're expected to file a notarised Sworn Declaration instead — and either way the
rest of the law still applies to you: a Data Protection Officer, a privacy notice (you have one),
keeping the data secure, and answering people who ask for their data or its deletion.

Ask the NPC directly at **privacy.gov.ph** or have a lawyer look at it. Don't take this paragraph
as the answer — it's a starting point, not advice.

---

## 5. Every week or so, once it's live

- **Download a backup:** `bash deploy/get-backup.sh ubuntu@SERVER_IP ~/Downloads/your-key.key`
  Put the zip in iCloud or Google Drive. It holds the whole Turso database; the pictures stay in Supabase.
- **Look at `/admin`:** who's using it, and what they wrote in Feedback.
- **Watch the bills:** Oracle, Turso, Supabase, Groq and Resend should all be ₱0 on their free
  plans. Set a $1 budget alert in the Oracle console, and glance at each dashboard's usage page.

---

## If something breaks

- `sudo docker compose -f ~/imy/deploy/docker-compose.yml logs -f imy` — what the site is saying.
- `python -m backend.preflight --url https://imy.pdyntech.com` — the launch checks, against the live site.
- `sudo docker compose -f ~/imy/deploy/docker-compose.yml exec imy python -m backend.services` — which outside service is failing.
- `python -m backend.admin link` — an admin sign-in link printed on the server, if email is down.
- Restart: `sudo docker compose -f ~/imy/deploy/docker-compose.yml restart imy`
