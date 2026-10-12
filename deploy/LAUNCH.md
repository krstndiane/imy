# Launch day

Everything in here is a thing only you can do — an account to make, a key to paste, a page to look
at on a real phone. Work down it in order and tick as you go. Nothing here needs code changes.

The code side is done: the site, the admin page at `/admin`, feedback, backups, and the launch
checks (`python -m backend.preflight`).

---

## 1. Before you deploy (15 minutes, on your Mac)

### 1.1 Gmail App Password  ☐
Without this, imy can send no email at all: no password resets, no admin sign-in links, no feedback
notices. On the live site you also need it once to prove the admin inbox is yours.

1. Sign in to **pdyntechnology@gmail.com**.
2. Turn on 2-Step Verification: myaccount.google.com → Security.
3. Go to **myaccount.google.com/apppasswords**, name it `imy`, and copy the 16 letters.
4. Put it in `.env` on your Mac, with no spaces:
   `IMY_SMTP_PASSWORD=abcdefghijklmnop`
5. Restart imy, then test: `python -m backend.mailer kristinadianeaviles@gmail.com`
   The test email should arrive within a minute (check spam the first time).

Keep those 16 letters somewhere safe. You'll be asked for them again when you set up the server.

### 1.2 Groq API key  ☐
Without it, replies come from imy's own reading of the uploaded messages — the vague ones.

1. **console.groq.com** → sign in → **API Keys** → Create API key.
2. Copy it (it starts `gsk_`) and put it in `.env`: `GROQ_API_KEY=gsk_...`
3. Restart imy. The Terminal should say `[imy] AI brain on: groq`.
4. Chat with someone you've set up and see whether the replies sound more like them.

Free, with a daily cap for the whole site. imy already shares that cap fairly between accounts
(`IMY_AI_DAILY_PER_ACCOUNT`), and falls back to its own replies when it runs out.

### 1.3 Last look on your own Mac  ☐
- `python -m backend.preflight` — everything should be OK except the ones about the live address.
- Open the homepage, the app and `/admin` once more.

---

## 2. Deploy

The steps are in the main README under **Putting imy on the internet**. In short:

1. Oracle Cloud account (Always Free), Ubuntu server, ports 80 and 443 open in the security list.
2. A free address at **duckdns.org** pointed at the server's IP.
3. From your Mac: `bash deploy/push-to-server.sh ubuntu@SERVER_IP ~/Downloads/your-key.key`
   It asks for the address, the Gmail App Password, who the admins are, and the Groq key.
4. `python -m backend.preflight --url https://your-address` — every line should say OK.

**Pick the smaller shape.** Oracle may reclaim an Always Free server that looks idle: over 7 days,
CPU under 20%, network under 20% *and* memory under 20%, all three at once. A 1 OCPU / 6 GB server
running imy sits well above the memory line; a 2 OCPU / 12 GB one with no visitors might not.
Smaller is safer here, and imy needs nothing like 12 GB.

---

## 3. The first hour it's live

### 3.1 Prove the admin page is yours  ☐
Open `https://your-address/admin`, type **kristinadianeaviles@gmail.com**, and use
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
Paste `https://your-address` into **developers.facebook.com/tools/debug/**, press *Scrape Again*,
and you should see the "Miss mo na?" card. Messenger and Facebook cache the old answer, so always
scrape once after a change. Then send the link to yourself on Messenger and look at the card.

### 3.4 Watch for it going down  ☐
Make a free account at **uptimerobot.com**, add an HTTPS monitor for
`https://your-address/api/health`, every 5 minutes, with email alerts to pdyntechnology@gmail.com.
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

### 4.2 A name of your own  ☐
`imy-something.duckdns.org` is free and works, but a name of your own looks like a real product and
makes your email less likely to land in spam.

- **.com** (imyapp.com and the like): roughly ₱600–900 a year. The sensible first choice.
- **.ph**: around US$42 a year (about ₱2,400), renewing near US$45. Lovely, but ten times the price.

Buy it anywhere (Namecheap, Porkbun, Cloudflare), point it at the server's IP, then change
`IMY_DOMAIN` and `IMY_PUBLIC_URL` in the server's `.env` and run `push-to-server.sh` again. Caddy
gets the new certificate by itself.

---

## 5. Every week or so, once it's live

- **Download a backup:** `bash deploy/get-backup.sh ubuntu@SERVER_IP ~/Downloads/your-key.key`
  Put the zip in iCloud or Google Drive. The server's own daily backups are on the same disk, so
  they don't save you from losing the server.
- **Look at `/admin`:** who's using it, and what they wrote in Feedback.
- **Watch the Oracle bill:** it should be ₱0. If it isn't, something slipped out of the free tier.
  Set a budget alert of $1 in the Oracle console so you hear about it the same day.

---

## If something breaks

- `sudo docker compose -f ~/imy/deploy/docker-compose.yml logs -f imy` — what the site is saying.
- `python -m backend.preflight --url https://your-address` — the launch checks, against the live site.
- `python -m backend.admin link` — an admin sign-in link printed on the server, if email is down.
- Restart: `sudo docker compose -f ~/imy/deploy/docker-compose.yml restart imy`
