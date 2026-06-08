"""
api/routes/whatsapp.py — WhatsApp Cloud API webhook

Handles two request types from Meta:
  GET  /whatsapp/webhook — one-time verification handshake when you save the
                           webhook URL in the Meta dashboard
  POST /whatsapp/webhook — incoming message from a WhatsApp user

Flow per incoming message:
  1. Extract sender phone number + message text from Meta's payload
  2. Derive session_id from phone number (one session per phone per day)
  3. Feed into existing process_turn() — identical to the /chat route
  4. Send response text back via Meta's send-message API

user_id  = sender's WhatsApp phone number  (e.g. "919876543210")
session_id = "<phone>-<YYYYMMDD>"          (resets each day automatically)
"""

import hashlib
import logging
from datetime import date

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from api.session_manager import process_turn
from config.settings import settings

log = logging.getLogger(__name__)
router = APIRouter()

WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _session_id(phone: str) -> str:
    """One session per phone number per calendar day."""
    day = date.today().strftime("%Y%m%d")
    raw = f"{phone}-{day}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _send_whatsapp_message(to: str, text: str) -> None:
    """Call Meta's send-message API to reply to the user."""
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


def _extract_message(payload: dict) -> tuple[str, str] | None:
    """
    Pull (phone_number, message_text) from Meta's webhook payload.
    Returns None if the event is not a text message (e.g. read receipts, status).
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        # Status updates (delivered, read) have no "messages" key — skip silently
        if "messages" not in value:
            return None

        msg = value["messages"][0]
        if msg.get("type") != "text":
            return None

        phone = msg["from"]
        text = msg["text"]["body"].strip()
        return phone, text
    except (KeyError, IndexError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

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


@router.post("/whatsapp/webhook")
async def whatsapp_incoming(request: Request):
    """
    Meta POSTs every incoming message here.
    Must return 200 quickly — processing is awaited inline (fast enough for testing).
    """
    # Always acknowledge immediately — Meta retries if it doesn't get 200
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ok"}, status_code=200)

    extracted = _extract_message(payload)
    if extracted is None:
        # Not a text message (status update, image, etc.) — ignore
        return JSONResponse({"status": "ok"}, status_code=200)

    phone, text = extracted
    user_id    = phone
    session_id = _session_id(phone)

    log.info("whatsapp: message from=%s text=%r", phone, text[:80])

    app      = request.app
    db_pool  = app.state.db_pool

    try:
        async with db_pool.acquire() as conn:
            result, _ = await process_turn(
                message=text.strip()[:2000],
                user_id=user_id,
                session_id=session_id,
                db_conn=conn,
                db_conn_pool=db_pool,
                app_state=app.state,
            )

        response_text = (result.get("response") or {}).get("text") or ""

        # Truncate to WhatsApp's 4096 char limit
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

    return JSONResponse({"status": "ok"}, status_code=200)
