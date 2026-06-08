# WhatsApp Testing Plan — Preventify Diabetes Educator AI

**Date:** 2026-06-06  
**Status:** Local development complete, moving to WhatsApp testing  
**Goal:** Test the existing Phase 1 + Phase 2 pipeline via WhatsApp (free, developer setup)

---

## What We Have So Far

- Full RAG pipeline built and working on localhost
- Phase 1 (context engine) + Phase 2 (retrieval + response) complete
- FastAPI backend running locally
- Web chat frontend working for base-model validation
- WhatsApp is the next testing channel (before clinical sign-off and production)

---

## Why WhatsApp Testing

The product's north-star delivery channel is WhatsApp. Testing on it early validates:
- Message formatting in a real WhatsApp environment
- Response latency end-to-end
- Conversation flow outside a controlled web UI
- Any edge cases in the adapter layer before production

---

## Architecture — What Changes

Nothing in the core pipeline changes. A thin **WhatsApp adapter** is added:

```
User messages test WhatsApp number (their personal phone)
  → Meta Cloud API receives message
  → POSTs to your webhook endpoint (/webhook on your FastAPI server)
  → Webhook extracts phone number + message text
  → Feeds into existing Phase 1 → Phase 2 pipeline
  → Response sent back via Meta's send-message API
  → User receives reply on WhatsApp
```

---

## Tools Required (All Free)

| Tool | Purpose | Cost |
|------|---------|------|
| Meta for Developers account | API access, test number, webhook config | Free |
| ngrok | Exposes localhost to internet so Meta can reach it | Free tier |
| Your personal WhatsApp number | To receive and send test messages | Already have |
| Facebook account | To log in to Meta for Developers | Already have |

---

## Step-by-Step Setup

### Step 1 — Expose Localhost with ngrok

1. Sign up at `ngrok.com` (free)
2. Download and install ngrok
3. Run in terminal:
   ```
   ngrok http 8000
   ```
   *(Replace 8000 with your FastAPI port)*
4. Copy the HTTPS URL it gives you — example: `https://abc123.ngrok-free.app`
5. Keep this terminal open while testing

> **Note:** On the free tier, this URL changes every time you restart ngrok. Update the Meta webhook URL each session.

---

### Step 2 — Create App on Meta for Developers

1. Go to `developers.facebook.com`
2. Log in with your personal Facebook account
3. Click **My Apps → Create App**
4. Choose **Other → Business**
5. Give it a name (e.g. `PreventifyBot`) → Create App

---

### Step 3 — Add WhatsApp Product

1. On your app dashboard, find **WhatsApp** → click **Set up**
2. You will see a **test phone number** — Meta provides this automatically (no SIM needed, no company number needed)
3. Under **To**, add your personal WhatsApp number as a test recipient
4. Send a test message from the dashboard to verify delivery

---

### Step 4 — Configure Webhook

1. Go to **WhatsApp → Configuration → Webhook**
2. Fill in:
   - **Webhook URL:** `https://abc123.ngrok-free.app/webhook`
   - **Verify Token:** any string you choose, e.g. `preventify123`
3. Click **Verify and Save**
4. Under **Webhook Fields**, subscribe to **messages**

---

### Step 5 — Add Webhook Route to FastAPI

Add a `/webhook` route to your existing FastAPI app. This route:
- Handles Meta's verification GET request (one-time handshake)
- Handles incoming POST requests (actual messages from users)
- Extracts the message text and sender phone number
- Passes message into your Phase 1 → Phase 2 pipeline
- Calls Meta's API to send the response back

This is the only new code needed — everything else (chunking, reranking, context engine, response generation) stays exactly as is.

---

### Step 6 — Test End-to-End

1. Make sure your FastAPI server is running locally
2. Make sure ngrok is running and URL is set in Meta dashboard
3. From your personal WhatsApp, send a diabetes-related question to the test number
4. Watch it flow through your pipeline and respond

---

## What You Do NOT Need

- A company WhatsApp number (test number is provided by Meta)
- WhatsApp Business App on your phone (that's for manual chatting, not API bots)
- Any paid service or BSP (Twilio, Gupshup, etc.)
- Deployed server (ngrok handles the tunnel from Meta to localhost)

---

## Limitations of This Testing Setup

| Limitation | Impact |
|-----------|--------|
| ngrok URL changes on restart | Manually update Meta webhook URL each session |
| 5 test recipient numbers max | Fine for internal team testing |
| 1,000 free conversations/month on Meta | More than enough for testing phase |
| No template messages needed | User-initiated conversations are free-form |

---

## After Testing — Path to Production

1. Deploy FastAPI to a cloud server (e.g. Railway, Render, or a VPS) — gets a permanent HTTPS URL
2. Replace ngrok URL with production URL in Meta dashboard
3. Apply for a real WhatsApp Business phone number via Meta
4. Submit message templates for approval if you want to initiate conversations
5. This comes **after clinical sign-off** per the project plan (B2 blocker)

---

## CLAUDE.md Reference

Per the project architecture document, WhatsApp integration happens **after clinical sign-off**. This testing phase is for developer validation only — the web chat frontend remains the primary channel until B2 (nutrition placeholders) and B4 (RMP loop design) are resolved.

---

*Document generated: 2026-06-06*  
*Project: Preventify Diabetes Educator AI — Sugar Care Clinics*
