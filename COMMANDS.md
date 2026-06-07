# WC2026 — Shared Vocabulary

## 🌐 Dashboard URL (always)
**https://wc2026-nine-iota.vercel.app**
Backend: https://wc2026-backend-production-dd79.up.railway.app

---

Commands you can say to me (Claude) during any session.

---

## 🔄 RESET
**What it does:** Wipes all bets + P&L history, resets bankroll to €500.  
**What it keeps:** All matches, odds, news, AI recommendations, agent name, settings.  
**When to use:** Before the tournament starts, or to wipe test bets.

**I will run:**
```
POST https://wc2026-backend-production-dd79.up.railway.app/api/reset
```
Then the dashboard refreshes automatically via WebSocket.

---

## 🚀 DEPLOY
**What it does:** I commit + give you the exact git command to push to GitHub → Vercel + Railway auto-deploy. Once Railway is back up, I automatically trigger RUN AGENT so fresh recommendations are live immediately.  
**Use when:** I've made code changes you want to go live.

---

## 🤖 RUN AGENT
**What it does:** Triggers the AI analysis cycle manually (Betting Agent → Audit Agent → new recommendations).  
**Normally:** Runs automatically at 08:00 and 16:00 UTC every day.  
**I will call:** `POST /api/run-agent`

---

## 📊 STATUS
**What it does:** I check Railway health, portfolio, open bets, and tell you if anything is broken.

---

## 🐛 AUDIT
**What it does:** I open the dashboard in Chrome, test every flow end-to-end, and report bugs with fixes.

---

## URLs
- **Dashboard:** https://wc2026-nine-iota.vercel.app
- **Backend:** https://wc2026-backend-production-dd79.up.railway.app
- **Railway logs:** railway.app → your project → Deployments → View Logs
- **Supabase:** supabase.com → your project → Table Editor
