"""
Construction SaaS – Twilio WhatsApp Bot  (v2.0 – Responses API)
================================================================
Receives WhatsApp messages via Twilio webhook.
  • PDF  → inline file_search via OpenAI Responses API → Tender Risk Summary
  • Voice Note → Whisper transcription → Responses API reply → TTS audio
  • Text → Responses API reply
"""

import os
import re
import base64
import uuid
import logging
import threading
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import FileResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from openai import OpenAI

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv(override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_SID     = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("whatsapp-bot")

# ── Directories ───────────────────────────────────────────────────────────────
MEDIA_DIR = Path("temp_media")
MEDIA_DIR.mkdir(exist_ok=True)

# ── Clients ───────────────────────────────────────────────────────────────────
openai_client = OpenAI(api_key=OPENAI_API_KEY)
twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)

# ── GPT Model ────────────────────────────────────────────────────────────────
MODEL = "gpt-4o"   # Use widely available model on Responses API


# ═══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT – embedded in code (no more separate Assistant dashboard)
# ═══════════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a construction tender analysis assistant for licensed contractors in India. Your job is to analyze tender documents and explain risks, financial exposure, and compliance requirements clearly so contractors can decide whether to bid.

TONE AND STYLE:
• Use simple, practical language
• Avoid corporate jargon, academic language, or generic AI phrasing
• Be concise but complete
• Focus on decision usefulness, not theoretical explanation

LANGUAGE HANDLING:
• Automatically detect the user's language
• Respond in the same language: Hindi → reply in Hindi, English → reply in English
• If mixed, default to English unless asked otherwise

MANDATORY EXTRACTION FROM TENDER PDFs:
Always identify and clearly explain:
• Submission deadline and important dates
• EMD, performance security, or deposits required
• Penalty, liquidated damages, or termination clauses
• Eligibility criteria (financial, technical, licensing)
If any information is missing, state: "Not clearly specified in the document."

RISK ANALYSIS:
Classify risks into: Financial, Timeline, Compliance, Contractual/Legal
For each risk: explain practical impact and possible financial/operational consequence
Highlight unusually strict clauses

RISK SEVERITY SCORING:
Provide an overall tender risk score: Low / Moderate / High / Very High
Base on: penalty severity, security deposit size, payment terms, technical complexity, contract obligations
Explain why you gave that score

OUTPUT STRUCTURE (Always Follow):
1. Key Tender Details
2. Major Risks Explained Simply
3. Financial Exposure Summary
4. Eligibility Summary
5. Final Risk Score
6. Recommendation: Safe to bid / Bid cautiously / High risk, review carefully

CRITICAL – WHATSAPP FORMATTING RULES:
• Keep total response under 1400 characters (HARD LIMIT — Twilio rejects longer messages)
• Use *bold* (single asterisk) for emphasis — NOT **double asterisk**
• Use • or ▸ for bullet points
• Use emojis for visual structure (🔴 High, 🟡 Moderate, 🟢 Low)
• NO markdown headers (no # or ##)
• NO horizontal rules (no ---)
• NO markdown tables
• NO citation brackets like【4:2†source】
• Short bullet points only — no long paragraphs
• Prioritize readability on mobile screens

ACCURACY AND TRANSPARENCY:
• Do not guess missing data — if uncertain, say so clearly
• Do not fabricate legal interpretations

CONTRACTOR-FOCUSED INSIGHT:
Translate clauses into practical impact: cash flow requirements, equipment/logistics implications, delay penalty exposure, compliance burden."""


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀  WhatsApp Bot is LIVE  (Responses API v2.0)")
    log.info(f"   Model: {MODEL}")
    yield
    log.info("🛑  Shutting down …")


app = FastAPI(
    title="Construction SaaS – WhatsApp Bot",
    version="2.0.0",
    lifespan=lifespan,
)

# Store ngrok public URL (set at startup by run_server)
_config = {"public_url": ""}


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – download media from Twilio (requires auth)
# ═══════════════════════════════════════════════════════════════════════════════
async def download_twilio_media(media_url: str, extension: str) -> Path:
    """Download a media file from Twilio's servers and save locally."""
    filename = f"{uuid.uuid4().hex}.{extension}"
    filepath = MEDIA_DIR / filename
    async with httpx.AsyncClient(auth=(TWILIO_SID, TWILIO_TOKEN), follow_redirects=True) as client:
        resp = await client.get(media_url)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
    log.info(f"   ✅ Downloaded media → {filepath}")
    return filepath


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – Clean citations from OpenAI responses
# ═══════════════════════════════════════════════════════════════════════════════
def clean_response(text: str) -> str:
    """Remove OpenAI citation brackets like 【4:2†source.pdf】 from responses."""
    cleaned = re.sub(r'【[^】]*】', '', text)
    # Also remove any leftover double-asterisk bold → single asterisk
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', cleaned)
    # Remove markdown headers (### heading → *HEADING*)
    cleaned = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', cleaned, flags=re.MULTILINE)
    # Remove horizontal rules
    cleaned = re.sub(r'^-{3,}$', '', cleaned, flags=re.MULTILINE)
    # Clean up excessive blank lines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – Extract text from PDF using PyPDF
# ═══════════════════════════════════════════════════════════════════════════════
def extract_pdf_text(filepath: Path) -> str:
    """Extract all text from a PDF file using PyPDF."""
    from pypdf import PdfReader
    log.info(f"   📖 Extracting text from {filepath.name} …")
    reader = PdfReader(str(filepath))
    pages_text = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            pages_text.append(text)
    full_text = "\n\n".join(pages_text)
    # Truncate to ~60k chars to stay within context limits
    if len(full_text) > 60000:
        full_text = full_text[:60000] + "\n\n[... document truncated due to length ...]"
    log.info(f"   📄 Extracted {len(full_text)} chars from {len(reader.pages)} pages")
    return full_text


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – OpenAI Responses API (replaces old ask_assistant)
# ═══════════════════════════════════════════════════════════════════════════════
def ask_responses(user_message: str, file_path: Path | None = None) -> str:
    """Send a message (optionally with PDF text) to the OpenAI Responses API
    and return the text reply. No threads, no polling — single synchronous call."""
    log.info("   🤖 Calling Responses API …")

    # If a PDF is provided, extract its text and prepend to the message
    if file_path and file_path.exists() and file_path.suffix.lower() == ".pdf":
        pdf_text = extract_pdf_text(file_path)
        user_message = (
            f"{user_message}\n\n"
            f"--- START OF TENDER DOCUMENT ---\n"
            f"{pdf_text}\n"
            f"--- END OF TENDER DOCUMENT ---"
        )

    # Single API call — no threads, no runs, no polling, no file upload
    try:
        log.info(f"   📨 Sending to {MODEL}, message length: {len(user_message)} chars")
        response = openai_client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=user_message,
        )
        log.info(f"   📩 Response ID: {response.id}")

        # Extract the text output
        reply = response.output_text
        log.info(f"   ✅ Response received ({len(reply)} chars)")

        # Clean up citations and fix formatting
        reply = clean_response(reply)
        return reply

    except Exception as e:
        log.exception("Error calling Responses API")
        return f"⚠️ Error getting response: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – Whisper transcription
# ═══════════════════════════════════════════════════════════════════════════════
def transcribe_audio(filepath: Path) -> str:
    """Transcribe an audio file using OpenAI Whisper."""
    log.info(f"   🎤 Transcribing {filepath.name} …")
    with open(filepath, "rb") as audio_file:
        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    text = transcript.text
    log.info(f"   📝 Transcript: {text[:120]}…")
    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – TTS → send audio back via Twilio
# ═══════════════════════════════════════════════════════════════════════════════
def text_to_speech(text: str) -> Path:
    """Convert text to speech using OpenAI TTS and save as mp3."""
    log.info("   🔊 Generating TTS …")
    filename = f"tts_{uuid.uuid4().hex}.mp3"
    filepath = MEDIA_DIR / filename
    response = openai_client.audio.speech.create(
        model="tts-1",
        voice="nova",
        input=text[:4096],
    )
    response.stream_to_file(str(filepath))
    log.info(f"   ✅ TTS saved → {filepath}")
    return filepath


def send_audio_via_twilio(to: str, from_: str, audio_path: Path):
    """Send an audio message back to the WhatsApp user via Twilio API."""
    audio_url = f"{_config['public_url']}/media/{audio_path.name}"
    log.info(f"   📤 Sending audio → {audio_url}")
    twilio_client.messages.create(
        to=to,
        from_=from_,
        media_url=[audio_url],
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER – Send reply back to WhatsApp (handles chunking)
# ═══════════════════════════════════════════════════════════════════════════════
def send_whatsapp_reply(reply: str, to: str, from_: str, twiml: MessagingResponse | None = None):
    """Send a reply via TwiML (short) or Twilio REST API (long/chunked).
    Twilio enforces a hard 1600 char limit per message (emojis count 2-4x)."""
    MAX_CHARS = 1500  # Safe limit under Twilio's 1600 (emoji overhead)

    if twiml is not None and len(reply) <= 1500:
        # Short reply → use TwiML inline response
        twiml.message(reply)
    elif len(reply) <= MAX_CHARS:
        # Medium reply → send as one REST API message
        twilio_client.messages.create(to=to, from_=from_, body=reply)
    else:
        # Long reply → chunk into MAX_CHARS segments
        remaining = reply
        while remaining:
            chunk = remaining[:MAX_CHARS]
            remaining = remaining[MAX_CHARS:]
            twilio_client.messages.create(to=to, from_=from_, body=chunk)


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND – Process PDF in background thread (avoids Twilio 15s timeout)
# ═══════════════════════════════════════════════════════════════════════════════
def process_pdf_background(media_url: str, to: str, from_: str):
    """Download PDF, analyze with OpenAI, send result via Twilio.
    Runs in a background thread so the webhook can return immediately."""
    try:
        log.info("   📥 [BG] Downloading PDF …")
        # Download synchronously (we're in a thread, not async)
        import httpx as httpx_sync
        with httpx_sync.Client(auth=(TWILIO_SID, TWILIO_TOKEN), follow_redirects=True) as client:
            resp = client.get(media_url)
            resp.raise_for_status()

        filename = f"{uuid.uuid4().hex}.pdf"
        pdf_path = MEDIA_DIR / filename
        pdf_path.write_bytes(resp.content)
        log.info(f"   ✅ [BG] Downloaded PDF → {pdf_path}")

        # Progress update
        twilio_client.messages.create(
            to=to, from_=from_,
            body="🔍 Analyzing tender risks… almost done!"
        )

        # Call Responses API with inline file
        reply = ask_responses(
            user_message=(
                "Please review the attached tender PDF document. "
                "Provide a concise Tender Risk Summary following the output structure. "
                "Keep the total response under 1400 characters. This is critical — do NOT exceed 1400 characters. "
                "Use WhatsApp-friendly formatting only."
            ),
            file_path=pdf_path,
        )

        # Send the result back via Twilio REST API
        send_whatsapp_reply(reply, to=to, from_=from_)

        # Cleanup
        pdf_path.unlink(missing_ok=True)
        log.info("   ✅ [BG] PDF processing complete")

    except Exception as e:
        log.exception("[BG] Error processing PDF")
        twilio_client.messages.create(
            to=to, from_=from_,
            body=f"⚠️ Error analysing PDF: {e}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND – Process voice note in background thread
# ═══════════════════════════════════════════════════════════════════════════════
def process_voice_background(media_url: str, content_type: str, to: str, from_: str):
    """Download audio, transcribe, get AI reply, send TTS back.
    Runs in a background thread so the webhook can return immediately."""
    try:
        # Determine extension
        ext = "ogg"
        if "mp3" in content_type.lower():
            ext = "mp3"
        elif "mp4" in content_type.lower():
            ext = "mp4"
        elif "wav" in content_type.lower():
            ext = "wav"

        log.info(f"   🎙️ [BG] Downloading audio (.{ext}) …")
        import httpx as httpx_sync
        with httpx_sync.Client(auth=(TWILIO_SID, TWILIO_TOKEN), follow_redirects=True) as client:
            resp = client.get(media_url)
            resp.raise_for_status()

        filename = f"{uuid.uuid4().hex}.{ext}"
        audio_path = MEDIA_DIR / filename
        audio_path.write_bytes(resp.content)
        log.info(f"   ✅ [BG] Downloaded audio → {audio_path}")

        # Transcribe with Whisper
        transcript = transcribe_audio(audio_path)

        # Send transcript to Responses API
        log.info("   🤖 [BG] Sending transcript to Responses API …")
        reply = ask_responses(
            user_message=(
                f"The user sent a voice message. Here is the transcription:\n\n"
                f'"'+ transcript +'"\n\n'
                f"Please respond helpfully. Keep response under 2000 characters. "
                f"Use WhatsApp-friendly formatting."
            ),
        )
        log.info(f"   💬 [BG] Reply: {reply[:120]}…")

        # Convert reply to speech
        tts_path = text_to_speech(reply)

        # Send the text reply
        twilio_client.messages.create(
            to=to, from_=from_,
            body=f"📝 *Transcript:*\n{transcript}\n\n💬 *Reply:*\n{reply[:3000]}",
        )

        # Send the audio reply
        send_audio_via_twilio(to=to, from_=from_, audio_path=tts_path)

        # Cleanup downloaded audio (keep TTS for Twilio to fetch)
        audio_path.unlink(missing_ok=True)
        log.info("   ✅ [BG] Voice processing complete")

    except Exception as e:
        log.exception("[BG] Error processing voice note")
        twilio_client.messages.create(
            to=to, from_=from_,
            body=f"⚠️ Error processing voice note: {e}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ENDPOINT – serve temp media so Twilio can fetch TTS audio
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/media/{filename}")
async def serve_media(filename: str):
    filepath = MEDIA_DIR / filename
    if not filepath.exists():
        return Response(status_code=404, content="Not found")
    return FileResponse(filepath, media_type="audio/mpeg")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENDPOINT – Twilio WhatsApp Webhook
# ═══════════════════════════════════════════════════════════════════════════════
@app.post("/webhook")
async def whatsapp_webhook(
    From: str = Form(""),
    To: str = Form(""),
    Body: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: str = Form(""),
    MediaContentType0: str = Form(""),
):
    """
    Main webhook for incoming WhatsApp messages via Twilio.
    Uses OpenAI Responses API — no threads, no polling, single-call.
    """
    log.info(f"━━━ Incoming from {From} ━━━")
    log.info(f"   Body : {Body[:200]}")
    log.info(f"   Media: {NumMedia} | Type: {MediaContentType0}")

    twiml = MessagingResponse()
    num_media = int(NumMedia)

    # ── CASE 1: PDF attachment ────────────────────────────────────────────
    if num_media > 0 and "pdf" in MediaContentType0.lower():
        log.info("   📄 PDF detected – launching background processing …")

        # Return TwiML immediately → Twilio won't timeout
        twiml.message("📄 Got your PDF! Reading the document… ⏳")

        # Start heavy processing in background thread
        thread = threading.Thread(
            target=process_pdf_background,
            args=(MediaUrl0, From, To),
            daemon=True,
        )
        thread.start()

        return Response(content=str(twiml), media_type="application/xml")

    # ── CASE 2: Voice Note ────────────────────────────────────────────────
    if num_media > 0 and ("audio" in MediaContentType0.lower() or "ogg" in MediaContentType0.lower()):
        log.info("   🎙️ Voice note detected – launching background processing …")

        # Return TwiML immediately
        twiml.message("🎙️ Got your voice note! Transcribing & processing… ⏳")

        # Start heavy processing in background thread
        thread = threading.Thread(
            target=process_voice_background,
            args=(MediaUrl0, MediaContentType0, From, To),
            daemon=True,
        )
        thread.start()

        return Response(content=str(twiml), media_type="application/xml")

    # ── CASE 3: Plain text message ────────────────────────────────────────
    if Body.strip():
        log.info("   💬 Text message – forwarding to Responses API …")
        try:
            reply = ask_responses(user_message=Body)
            send_whatsapp_reply(reply, to=From, from_=To, twiml=twiml)
        except Exception as e:
            log.exception("Error processing text message")
            twiml.message(f"⚠️ Error: {e}")

        return Response(content=str(twiml), media_type="application/xml")

    # ── Fallback ──────────────────────────────────────────────────────────
    twiml.message(
        "👋 Hi! I'm your Construction Tender Assistant.\n\n"
        "Send me:\n"
        "📄 A *PDF* for a Tender Risk Summary\n"
        "🎙️ A *Voice Note* for a spoken reply\n"
        "💬 Or just *type a message*!"
    )
    return Response(content=str(twiml), media_type="application/xml")


# ═══════════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/")
async def health():
    return {
        "status": "running",
        "bot": "Construction SaaS WhatsApp Bot v2.0",
        "api": "OpenAI Responses API",
        "model": MODEL,
        "webhook": f"{_config['public_url']}/webhook" if _config['public_url'] else "ngrok not started",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT – start uvicorn + ngrok
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn

    PORT = 8000

    # ── Start ngrok tunnel via pyngrok ────────────────────────────────────
    log.info(f"🌐 Starting ngrok tunnel on port {PORT} …")
    try:
        from pyngrok import ngrok, conf

        # Set authtoken explicitly
        conf.get_default().auth_token = "3AAgKgSJvMAfL9aopoZHLxRy0v4_5KU2GxDM9yg7hZHpPMXAd"

        tunnel = ngrok.connect(PORT)
        _config["public_url"] = tunnel.public_url
        log.info(f"✅ ngrok tunnel active: {_config['public_url']}")
        log.info(f"📌 Set your Twilio webhook to: {_config['public_url']}/webhook")
    except Exception as e:
        log.warning(f"⚠️  ngrok failed ({e}). Running locally only on port {PORT}.")
        _config["public_url"] = f"http://localhost:{PORT}"

    log.info(f"🔗 PUBLIC_URL = {_config['public_url']}")

    # ── Start uvicorn ─────────────────────────────────────────────────────
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
