"""
api/routes/whatsapp.py — WhatsApp Cloud API webhook

Handles two request types from Meta:
  GET  /whatsapp/webhook — one-time verification handshake when you save the
                           webhook URL in the Meta dashboard
  POST /whatsapp/webhook — incoming message from a WhatsApp user

Flow per incoming message:
  1. Detect new user → send welcome buttons (chat flow)
  2. Check lead state (location_pending) → handle district reply
  3. Handle button replies (appointment / daily tip)
  4. Route through RAG pipeline for questions
  5. After response: check lead trigger → ask for district if conditions met

user_id  = sender's WhatsApp phone number  (e.g. "919876543210")
session_id = "<phone>-<YYYYMMDD>"          (resets each day automatically)
"""

import asyncio
import hashlib
import logging
from datetime import date

import httpx
from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse

from api.audit_logger import write_audit_log
from api.session_manager import process_turn
from config.settings import settings
from engine.translator import translate_to_english

log = logging.getLogger(__name__)
router = APIRouter()

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"


# ─────────────────────────────────────────────────────────────────────────────
# Static content
# ─────────────────────────────────────────────────────────────────────────────

_WELCOME_TEXT = (
    "നമസ്കാരം! ഞാൻ Preventify-യുടെ ഡയബറ്റിസ് അസിസ്റ്റന്റ് ആണ്. "
    "എങ്ങനെ സഹായിക്കട്ടെ?"
)

_WELCOME_BUTTONS = [
    {"id": "btn_ask",         "title": "Ask a Question"},
    {"id": "btn_appointment", "title": "Book Appointment"},
    {"id": "btn_tips",        "title": "Daily Tips"},
]

_APPT_ASK_NAME = (
    "Appointment book ചെ യ്യാം! 😊\n"
    "ആദ്യം നിങ്ങളുടെ പേര് പറയൂ."
)

_APPT_ASK_DISTRICT = (
    "നന്ദി! നിങ്ങൾ ഏത് ജില്ലയിൽ ആണ്? "
    "(Example: Ernakulam, Thrissur, Kozhikode...)"
)

_APPT_CONFIRM = (
    "✅ {name} — നിങ്ങളുടെ appointment request register ചെ യ്തു!\n"
    "Sugar Care Clinics-ൽ നിന്ന് ഉടൻ {district}-ൽ contact ചെ യ്യും.\n"
    "🌐 www.sugarcareclinics.com"
)

# 7 tips — one per weekday (Monday=0 … Sunday=6)
_DAILY_TIPS = [
    # Monday
    "💡 ഇന്നത്തെ tip: Chaaya (ചായ) കുടിക്കുമ്പോൾ sugar ഇടാതെ കുടിക്കൂ. "
    "ദിവസം 4–6 cups + 2 spoon sugar = 40–50 g extra sugar! "
    "ഇത് ഒഴിവാക്കിയാൽ HbA1c 0.5–1 point കുറയാൻ സഹായിക്കും.",
    # Tuesday
    "💡 ഇന്നത്തെ tip: ഭക്ഷണം കഴിച്ചതിന് ശേഷം 10–15 minutes നടക്കൂ. "
    "ഇത് blood sugar spike 20–30% കുറയ്ക്കും — gym വേണ്ട, വീടിനു ചുറ്റും മതി.",
    # Wednesday
    "💡 ഇന്നത്തെ tip: Matta rice white rice-നേക്കാൾ നല്ലതാണ്. "
    "GI കുറവ്, fiber കൂടുതൽ — ഒരു ladle-ൽ കൂടരുത് ഒരു നേരം.",
    # Thursday
    "💡 ഇന്നത്തെ tip: കാലിൽ ദിവസവും നോക്കൂ — cuts, blisters, redness. "
    "Diabetic foot problems early ആയി കണ്ടുപിടിച്ചാൽ 90% cases home-ൽ തന്നെ treat ചെ യ്യാം.",
    # Friday
    "💡 ഇന്നത്തെ tip: Metformin ഭക്ഷണത്തിനൊപ്പം കഴിക്കൂ — ഉദര അസ്വസ്ഥത കുറയ്ക്കാം. "
    "Doctor-ന്റെ നിർദ്ദേശം മാറ്റരുത്; dose skip ചെ യ്യുന്നത് sugar control-ഇനെ ബാധിക്കും.",
    # Saturday
    "💡 ഇന്നത്തെ tip: Mathi (sardine), ayala (mackerel) ആഴ്ചയിൽ 3 തവണ കഴിക്കൂ. "
    "Omega-3 heart risk കുറയ്ക്കും — Diabetics-ന് ഹൃദ്രോഗ സാധ്യത 2–4× കൂടുതലാണ്.",
    # Sunday
    "💡 ഇന്നത്തെ tip: ഉറക്കം 7–8 മണിക്കൂർ ഉറപ്പാക്കൂ. "
    "ഉറക്കക്കുറവ് insulin resistance കൂട്ടും — fasting sugar 10–20 mg/dL ഉയരാൻ ഇടയാക്കും.",
]


def _daily_tip() -> str:
    return _DAILY_TIPS[date.today().weekday()]

_LOCATION_ASK_TEXT = (
    "നിങ്ങൾക്ക് അടുത്തുള്ള Sugar Care Clinic കണ്ടുപിടിക്കാൻ "
    "സഹായിക്കട്ടെ? നിങ്ങൾ ഏത് ജില്ലയിൽ ആണ്? "
    "(Example: Ernakulam, Thrissur, Kozhikode...)"
)

# Districts where Sugar Care Clinics operate or are planned
_CLINIC_REPLIES: dict[str, str] = {
    "ernakulam": (
        "Ernakulam-ൽ ഞങ്ങളുടെ Sugar Care Clinic ഉണ്ട്! "
        "Appointment book ചെ യ്യാൻ ഞങ്ങളുടെ team ഉടൻ contact ചെ യ്യും."
    ),
    "thrissur": (
        "Thrissur-ൽ ഞങ്ങൾ ഉടൻ clinic തുടങ്ങും! "
        "Launch ആകുമ്പോൾ നിങ്ങൾക്ക് first appointment നൽകും."
    ),
}

_CLINIC_REPLY_FALLBACK = (
    "{district}-ൽ ഞങ്ങൾ expand ചെ യ്യാൻ plan ചെ യ്യുന്നു. "
    "ഞങ്ങളുടെ team നിങ്ങൾക്ക് ഉടൻ contact ചെ യ്യും."
)


# ─────────────────────────────────────────────────────────────────────────────
# WhatsApp API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _session_id(phone: str) -> str:
    """One session per phone number per calendar day."""
    day = date.today().strftime("%Y%m%d")
    raw = f"{phone}-{day}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _send_whatsapp_message(to: str, text: str) -> None:
    """Send a plain text message via Meta's send-message API."""
    url = f"{WHATSAPP_API_URL}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            log.error("whatsapp: send failed to=%s status=%d body=%s",
                      to, resp.status_code, resp.text)
        else:
            log.info("whatsapp: message sent to=%s", to)


async def _send_whatsapp_interactive(to: str, body_text: str, buttons: list[dict]) -> None:
    """Send an interactive reply-button message. buttons = [{"id": "...", "title": "..."}]"""
    url = f"{WHATSAPP_API_URL}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            log.error("whatsapp: interactive send failed to=%s status=%d body=%s",
                      to, resp.status_code, resp.text)
        else:
            log.info("whatsapp: interactive message sent to=%s", to)


def _extract_message(payload: dict) -> tuple[str, str, str | None] | None:
    """
    Pull (phone_number, message_text, button_id) from Meta's webhook payload.
    Returns None if the event is not a message we handle.
    button_id is None for regular text messages; set to the button's id for
    interactive button_reply events.
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        # Status updates (delivered, read) have no "messages" key — skip silently
        if "messages" not in value:
            return None

        msg = value["messages"][0]
        phone = msg["from"]
        msg_type = msg.get("type")

        if msg_type == "text":
            text = msg["text"]["body"].strip()
            return phone, text, None

        if msg_type == "interactive":
            interactive = msg.get("interactive", {})
            if interactive.get("type") == "button_reply":
                btn = interactive["button_reply"]
                return phone, btn.get("title", ""), btn.get("id", "")

        return None
    except (KeyError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Lead capture DB helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _session_has_turns(phone: str, session_id: str, pool) -> bool:
    """
    Return True if an active conversation is in progress — meaning:
      - this session has at least one turn, AND
      - the most recent turn was within the last 4 hours.
    If the user has been inactive for 4+ hours (even within the same day),
    they get the welcome menu again on their next message.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt, MAX(created_at) AS last_at
                FROM session_turns
                WHERE user_id = $1 AND session_id = $2
                """,
                phone, session_id,
            )
            if not row or (row["cnt"] or 0) == 0:
                return False
            last_at = row["last_at"]
            if last_at is None:
                return False
            from datetime import timezone
            age_hours = (
                __import__("datetime").datetime.now(timezone.utc) - last_at
            ).total_seconds() / 3600
            return age_hours < 4
    except Exception as exc:
        log.error("whatsapp: _session_has_turns failed phone=%s — %s", phone, exc)
        return True  # fail-safe: skip welcome rather than loop


async def _mark_welcome_sent(phone: str, session_id: str, pool) -> None:
    """
    Insert a system sentinel into session_turns so the next message routes to
    the pipeline instead of triggering welcome again.
    Also ensures the user row exists (required by the FK on session_turns).
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                phone,
            )
            await conn.execute(
                """
                INSERT INTO session_turns (user_id, session_id, turn_number, role, content)
                VALUES ($1, $2, 0, 'system', '__welcome__')
                """,
                phone, session_id,
            )
    except Exception as exc:
        log.error("whatsapp: _mark_welcome_sent failed phone=%s — %s", phone, exc)


async def _get_user_lead_state(phone: str, pool) -> dict:
    """Load fields needed for lead capture trigger and location state."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT lifetime_score, highest_qds_ever, total_messages,
                       lead_status, location_hint
                FROM users WHERE user_id = $1
                """,
                phone,
            )
            return dict(row) if row else {}
    except Exception as exc:
        log.error("whatsapp: _get_user_lead_state failed phone=%s — %s", phone, exc)
        return {}


def _check_lead_trigger(state: dict) -> bool:
    """
    Return True if the location-ask should fire.
    Conditions (all three must hold):
      - lifetime_score >= 8  (cumulative QDS engagement)
      - highest_qds_ever >= 3  (at least one personal/distressed question)
      - total_messages >= 3  (minimum conversation depth)
      - location not yet captured
      - lead not already in a post-new_lead state
    """
    return (
        (state.get("lifetime_score") or 0) >= 8
        and (state.get("highest_qds_ever") or 0) >= 3
        and (state.get("total_messages") or 0) >= 3
        and not state.get("location_hint", "")
        and state.get("lead_status", "") == "new_lead"
    )


async def _set_lead_location_pending(phone: str, pool) -> None:
    """Transition lead_status → 'location_pending' before asking for district."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET lead_status = 'location_pending' WHERE user_id = $1",
                phone,
            )
    except Exception as exc:
        log.error("whatsapp: _set_lead_location_pending failed phone=%s — %s", phone, exc)


async def _update_user_location(phone: str, district: str, pool) -> None:
    """Store district and advance lead to 'qualified'."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET location_hint = $2, lead_status = 'qualified'
                WHERE user_id = $1
                """,
                phone, district,
            )
        log.info("whatsapp: location captured user=%s district=%r", phone, district)
    except Exception as exc:
        log.error("whatsapp: _update_user_location failed phone=%s — %s", phone, exc)


async def _set_appt_name_pending(phone: str, pool) -> None:
    """Start appointment flow — waiting for user's name."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET lead_status = 'appt_name' WHERE user_id = $1",
                phone,
            )
    except Exception as exc:
        log.error("whatsapp: _set_appt_name_pending failed phone=%s — %s", phone, exc)


async def _save_appt_name(phone: str, name: str, pool) -> None:
    """Store name, advance to waiting for district."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET appt_name = $2, lead_status = 'appt_district' WHERE user_id = $1",
                phone, name,
            )
    except Exception as exc:
        log.error("whatsapp: _save_appt_name failed phone=%s — %s", phone, exc)


async def _save_appt_district(phone: str, district: str, pool) -> dict:
    """Store district, mark lead qualified. Returns {name, district} for confirmation."""
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users
                SET location_hint = $2, lead_status = 'qualified'
                WHERE user_id = $1
                RETURNING appt_name
                """,
                phone, district,
            )
            name = (row["appt_name"] or "").strip() if row else ""
        log.info("whatsapp: appointment booked user=%s name=%r district=%r", phone, name, district)
        return {"name": name or "നിങ്ങൾ", "district": district}
    except Exception as exc:
        log.error("whatsapp: _save_appt_district failed phone=%s — %s", phone, exc)
        return {"name": "നിങ്ങൾ", "district": district}


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/whatsapp/test/welcome")
async def test_send_welcome(request: Request):
    """
    Dev-only: send the welcome button message to any phone number.
    Body: {"phone": "919876543210"}
    Lets you preview the chat flow without needing a brand-new number.
    """
    body = await request.json()
    phone = str(body.get("phone", "")).strip()
    if not phone:
        return JSONResponse({"error": "phone required"}, status_code=400)
    await _send_whatsapp_interactive(phone, _WELCOME_TEXT, _WELCOME_BUTTONS)
    return JSONResponse({"status": "sent", "to": phone})


@router.get("/whatsapp/webhook")
async def whatsapp_verify(request: Request):
    """
    Meta calls this once when you save the webhook URL in the dashboard.
    It sends hub.challenge and we must echo it back to prove we own the URL.
    """
    params = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        log.info("whatsapp: webhook verified successfully")
        return Response(content=challenge, media_type="text/plain")

    log.warning("whatsapp: webhook verification failed — token mismatch or wrong mode")
    return JSONResponse({"error": "verification failed"}, status_code=403)


async def _process_and_reply(phone: str, text: str, button_id: str | None, app_state) -> None:
    """
    Full message handler — decoupled from Meta's webhook timeout.

    State machine:
      new user           → welcome buttons
      location_pending   → treat message as district, store, reply with clinic info
      btn_appointment    → static booking text
      btn_tips           → static daily tip
      btn_ask / text     → RAG pipeline → after response, check lead trigger
    """
    pool = app_state.db_pool
    session_id = _session_id(phone)

    # ── 1. First message of session → welcome buttons (instant, no pipeline) ──
    if not await _session_has_turns(phone, session_id, pool):
        await _mark_welcome_sent(phone, session_id, pool)
        await _send_whatsapp_interactive(phone, _WELCOME_TEXT, _WELCOME_BUTTONS)
        log.info("whatsapp: welcome sent user=%s session=%s", phone, session_id)
        return

    # ── 2. Load lead state for routing decisions ───────────────────────────────
    lead_state = await _get_user_lead_state(phone, pool)

    # ── 3. Multi-step flows (appointment + location) — intercept before pipeline ─
    lead_status = lead_state.get("lead_status", "")

    if lead_status == "location_pending" and button_id is None:
        district = text.strip()
        await _update_user_location(phone, district, pool)
        reply = _CLINIC_REPLIES.get(
            district.lower(),
            _CLINIC_REPLY_FALLBACK.format(district=district),
        )
        await _send_whatsapp_message(phone, reply)
        return

    if lead_status == "appt_name" and button_id is None:
        await _save_appt_name(phone, text.strip(), pool)
        await _send_whatsapp_message(phone, _APPT_ASK_DISTRICT)
        return

    if lead_status == "appt_district" and button_id is None:
        info = await _save_appt_district(phone, text.strip(), pool)
        await _send_whatsapp_message(
            phone,
            _APPT_CONFIRM.format(name=info["name"], district=info["district"]),
        )
        return

    # ── 4. Static button replies (no pipeline) ─────────────────────────────────
    if button_id == "btn_appointment":
        await _set_appt_name_pending(phone, pool)
        await _send_whatsapp_message(phone, _APPT_ASK_NAME)
        return

    if button_id == "btn_tips":
        await _send_whatsapp_message(phone, _daily_tip())
        return

    if button_id == "btn_ask":
        await _send_whatsapp_message(
            phone,
            "നിങ്ങൾക്ക് എന്ത് സംശയമാണ്? ഭക്ഷണം, മരുന്ന്, വ്യായാമം, "
            "അല്ലെങ്കിൽ പൊതുവായ ഡയബറ്റിസ് കാര്യങ്ങൾ — ഏതും ചോദിക്കാം. 🙏",
        )
        return

    # plain text goes into the RAG pipeline

    # ── 5. RAG pipeline ────────────────────────────────────────────────────────
    english_text = translate_to_english(text.strip())
    user_id    = phone

    try:
        async with app_state.db_pool.acquire() as conn:
            result, turn_number = await process_turn(
                message=english_text[:2000],
                user_id=user_id,
                session_id=session_id,
                db_conn=conn,
                db_conn_pool=app_state.db_pool,
                app_state=app_state,
            )
            await write_audit_log(
                user_id=user_id,
                session_id=session_id,
                turn_number=turn_number,
                patient_message=english_text[:2000],
                result=result,
                db_conn=conn,
            )

        response_text = (result.get("response") or {}).get("text") or ""
        if len(response_text) > 4096:
            response_text = response_text[:4090] + "..."
        if not response_text:
            response_text = "Sorry, I wasn't able to process your question. Please try again."

        await _send_whatsapp_message(to=phone, text=response_text)

    except Exception as exc:
        log.exception("whatsapp: pipeline error user=%s — %s", phone, exc)
        await _send_whatsapp_message(
            to=phone,
            text="Something went wrong on our end. Please try again in a moment.",
        )
        return

    # ── 6. Lead trigger check — after response sent ───────────────────────────
    # Re-load state: lifetime_score and total_messages were updated during process_turn.
    try:
        updated_state = await _get_user_lead_state(phone, pool)
        if _check_lead_trigger(updated_state):
            await _set_lead_location_pending(phone, pool)
            await _send_whatsapp_message(phone, _LOCATION_ASK_TEXT)
            log.info("whatsapp: lead trigger fired user=%s — location ask sent", phone)
    except Exception as exc:
        log.error("whatsapp: lead trigger check failed user=%s — %s", phone, exc)


@router.post("/whatsapp/webhook")
async def whatsapp_incoming(request: Request, background_tasks: BackgroundTasks):
    """
    Meta POSTs every incoming message here.
    Returns 200 immediately — processing happens in background to avoid Meta's 20s timeout.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"}, status_code=200)

    extracted = _extract_message(payload)
    if extracted is None:
        return JSONResponse({"status": "ok"}, status_code=200)

    phone, text, button_id = extracted
    log.info("whatsapp: message from=%s text=%r button_id=%s", phone, text[:80], button_id)

    background_tasks.add_task(_process_and_reply, phone, text, button_id, request.app.state)

    return JSONResponse({"status": "ok"}, status_code=200)
