# 🏗️ Construction SaaS – WhatsApp Bot Architecture

> **Version:** 1.0.0
> **Last Updated:** 27 February 2026
> **Stack:** Python · FastAPI · Twilio · OpenAI · ngrok

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Technology Stack & Framework](#technology-stack--framework)
4. [Twilio WhatsApp API](#twilio-whatsapp-api)
5. [OpenAI Assistant Integration](#openai-assistant-integration)
6. [Server Architecture](#server-architecture)
7. [Workflow – End to End](#workflow--end-to-end)
8. [API Endpoints](#api-endpoints)
9. [Environment Configuration](#environment-configuration)
10. [Directory Structure](#directory-structure)

---

## 🔭 Overview

This application is a **WhatsApp-based AI chatbot** built for the **construction industry**. It receives messages from WhatsApp users via **Twilio**, processes them using **OpenAI's Assistant API**, and sends intelligent responses back.

### What It Does

| Input Type     | Processing                                   | Output                              |
|----------------|----------------------------------------------|--------------------------------------|
| 📄 **PDF**     | Upload to OpenAI → Assistant analyzes risks  | Tender Risk Summary (text)          |
| 🎙️ **Voice Note** | Whisper transcription → Assistant reply → TTS | Text reply + Audio reply (MP3)      |
| 💬 **Text**    | Forward to Assistant                         | AI-generated text reply             |

---

## 🏛️ High-Level Architecture

```
┌──────────────┐       ┌──────────────┐       ┌───────────────────────┐
│              │       │              │       │                       │
│   WhatsApp   │◄─────►│    Twilio    │◄─────►│   FastAPI Server      │
│   User       │  MSG  │   Platform   │ HTTP  │   (main.py)           │
│              │       │              │       │                       │
└──────────────┘       └──────┬───────┘       └───────┬───────────────┘
                              │                       │
                              │                       │  API Calls
                              │                       ▼
                              │               ┌───────────────────────┐
                              │               │                       │
                              │               │   OpenAI Platform     │
                              │               │                       │
                              │               │  ┌─────────────────┐  │
                              │               │  │   Assistant API  │  │
                              │               │  │   (GPT-4 based) │  │
                              │               │  └─────────────────┘  │
                              │               │  ┌─────────────────┐  │
                              │               │  │   Whisper API    │  │
                              │               │  │   (Speech→Text) │  │
                              │               │  └─────────────────┘  │
                              │               │  ┌─────────────────┐  │
                              │               │  │   TTS API        │  │
                              │               │  │   (Text→Speech) │  │
                              │               │  └─────────────────┘  │
                              │               │  ┌─────────────────┐  │
                              │               │  │   Files API      │  │
                              │               │  │   (PDF Upload)  │  │
                              │               │  └─────────────────┘  │
                              │               └───────────────────────┘
                              │
                      ┌───────▼───────┐
                      │    ngrok      │
                      │   Tunnel      │
                      │ (public URL)  │
                      └───────────────┘
```

---

## 🛠️ Technology Stack & Framework

### Framework: **FastAPI**

[FastAPI](https://fastapi.tiangolo.com/) is a modern, high-performance Python web framework for building APIs. It was chosen for this project because of:

- ⚡ **Async support** – Native `async/await` for handling Twilio webhooks and downloading media concurrently
- 📝 **Auto documentation** – Swagger UI available at `/docs` out of the box
- 🔒 **Type safety** – Python type hints for request validation
- 🚀 **Performance** – Built on Starlette and Uvicorn (ASGI), among the fastest Python frameworks

### Core Dependencies

| Package            | Version  | Purpose                                              |
|--------------------|----------|------------------------------------------------------|
| `fastapi`          | 0.115.0  | Web framework for API endpoints                     |
| `uvicorn[standard]`| 0.30.6   | ASGI server to run FastAPI                           |
| `twilio`           | 9.3.0    | Twilio SDK – send/receive WhatsApp messages          |
| `openai`           | 1.51.0   | OpenAI SDK – Assistant, Whisper, TTS, Files API      |
| `httpx`            | 0.27.2   | Async HTTP client for downloading Twilio media       |
| `python-dotenv`    | 1.0.1    | Load environment variables from `.env`               |
| `python-multipart` | 0.0.9    | Parse multipart form data (Twilio webhook payloads)  |
| `pyngrok`          | 7.2.1    | Programmatically start ngrok tunnels                 |

---

## 📱 Twilio WhatsApp API

### What Is Twilio?

Twilio is a **cloud communications platform** that provides APIs for SMS, voice, video, and **WhatsApp messaging**. In this project, Twilio acts as the **bridge between WhatsApp and our server**.

### How Twilio Works Here

```
User sends message on WhatsApp
        │
        ▼
Twilio receives the message
        │
        ▼
Twilio sends HTTP POST to our webhook URL
(POST /webhook with form data: From, To, Body, MediaUrl0, etc.)
        │
        ▼
Our server processes & responds with TwiML XML
        │
        ▼
Twilio delivers the reply back to WhatsApp user
```

### Key Twilio Components Used

1. **Webhook (Inbound)** – Twilio POSTs incoming WhatsApp messages to `/webhook`
2. **TwiML Response** – Server replies with XML that Twilio interprets as messages
3. **REST API (Outbound)** – For long messages or media, the server proactively sends messages via `twilio_client.messages.create()`
4. **Media Download** – Voice notes & PDFs are hosted on Twilio's servers; the bot downloads them using authenticated HTTP requests

### Twilio Webhook Form Fields

| Field              | Description                              |
|--------------------|------------------------------------------|
| `From`             | Sender's WhatsApp number (`whatsapp:+91...`) |
| `To`               | Bot's Twilio WhatsApp number             |
| `Body`             | Text content of the message              |
| `NumMedia`         | Number of media attachments (0, 1, …)    |
| `MediaUrl0`        | URL of the first media attachment        |
| `MediaContentType0`| MIME type of the attachment (e.g. `application/pdf`) |

---

## 🤖 OpenAI Assistant Integration

### What Is the OpenAI Assistant API?

The **Assistants API** allows you to build AI assistants within your applications. Unlike simple chat completions, Assistants support:

- **Persistent threads** – conversation context across multiple messages
- **File search** – the assistant can read uploaded documents (PDFs)
- **Tool use** – code interpreter, function calling, etc.

### OpenAI Services Used

#### 1. Assistant API (Core Intelligence)

```
Thread Created → Message Added → Run Started → Poll for Completion → Extract Reply
```

- **Thread**: A conversation session (new thread per message in this bot)
- **Message**: User's text + optional file attachments
- **Run**: Triggers the assistant to process the thread
- **Polling**: The server checks every 1 second (up to 120s) until the run completes

#### 2. Whisper API (Speech-to-Text)

- Model: `whisper-1`
- Accepts audio files (OGG, MP3, MP4, WAV)
- Returns transcribed text

#### 3. TTS API (Text-to-Speech)

- Model: `tts-1`
- Voice: `nova`
- Input: Up to 4,096 characters of text
- Output: MP3 audio file

#### 4. Files API (Document Upload)

- Purpose: `assistants`
- Uploads PDFs so the Assistant can search and analyze them using `file_search` tool

### Assistant Workflow

```
                    ┌─────────────────┐
                    │  Create Thread   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Add Message     │
                    │  (+ file_ids)    │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Create Run      │
                    │  (assistant_id)  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Poll Status     │◄──┐
                    │  every 1 second  │   │
                    └────────┬────────┘   │
                             │            │
                        completed?  ──No──┘
                             │
                           Yes
                             │
                    ┌────────▼────────┐
                    │  Extract Reply   │
                    │  (last message)  │
                    └─────────────────┘
```

---

## 🖥️ Server Architecture

### Entry Point (`main.py`)

The entire application lives in a single file `main.py` with the following structure:

```
main.py
├── Environment Loading (.env via dotenv)
├── Client Initialization
│   ├── OpenAI Client
│   └── Twilio Client
├── Helper Functions
│   ├── download_twilio_media()    → Downloads media from Twilio
│   ├── ask_assistant()            → Sends message to OpenAI Assistant
│   ├── transcribe_audio()         → Whisper speech-to-text
│   ├── text_to_speech()           → OpenAI TTS
│   └── send_audio_via_twilio()    → Sends audio back via Twilio API
├── API Endpoints
│   ├── GET  /                     → Health check
│   ├── GET  /media/{filename}     → Serve temp audio files for Twilio
│   └── POST /webhook              → Main Twilio WhatsApp webhook
└── Startup
    ├── ngrok tunnel setup
    └── Uvicorn ASGI server
```

### Lifespan Management

FastAPI's `lifespan` context manager handles startup and shutdown:

- **Startup**: Logs that the bot is live and shows the Assistant ID
- **Shutdown**: Logs shutdown message

### ngrok Tunnel

The server uses **pyngrok** to create a public HTTPS URL that tunnels to `localhost:8000`. This is necessary because:

- Twilio needs a **public URL** to send webhooks to
- During development, the server runs on `localhost`, which isn't publicly accessible
- ngrok provides a temporary public URL (e.g., `https://abc123.ngrok-free.app`)

---

## 🔄 Workflow – End to End

### Flow 1: PDF Tender Risk Analysis

```
1. User sends a PDF on WhatsApp
2. Twilio receives it and POSTs to /webhook
3. Server detects MediaContentType0 contains "pdf"
4. Server sends immediate acknowledgment: "📄 Got your PDF! Analysing…"
5. Server downloads PDF from Twilio (authenticated request)
6. Server uploads PDF to OpenAI Files API
7. Server creates an Assistant thread with the file attached
8. Server asks Assistant for a Tender Risk Summary:
   - Key risks identified
   - Risk severity (High / Medium / Low)
   - Mitigation recommendations
   - Overall risk rating
   - Clauses requiring special attention
9. Server polls until Assistant completes analysis
10. Server sends the risk summary back to user
    - If reply > 1500 chars → sends via Twilio REST API in chunks
    - If reply ≤ 1500 chars → sends via TwiML response
11. Cleanup: deletes the local PDF file
```

### Flow 2: Voice Note Processing

```
1. User sends a voice note on WhatsApp
2. Twilio receives it and POSTs to /webhook
3. Server detects audio/ogg content type
4. Server sends immediate acknowledgment: "🎙️ Got your voice note!…"
5. Server downloads the audio file from Twilio
6. Server transcribes audio using OpenAI Whisper → text
7. Server sends the transcript to OpenAI Assistant for a reply
8. Server converts the Assistant's reply to speech using OpenAI TTS
9. Server sends TWO messages back to the user:
   a. Text message with transcript + AI reply
   b. Audio message (MP3) with the spoken reply
10. Cleanup: deletes the downloaded audio (keeps TTS for Twilio to fetch)
```

### Flow 3: Plain Text Message

```
1. User sends a text message on WhatsApp
2. Twilio receives it and POSTs to /webhook
3. Server detects it's a plain text message (Body is not empty)
4. Server forwards the text to OpenAI Assistant
5. Server sends the Assistant's reply back to user
   - Long replies are chunked into 1600-char segments
```

### Flow 4: Fallback

```
1. User sends an empty or unsupported message
2. Server responds with a welcome/help message explaining capabilities
```

---

## 📡 API Endpoints

### `GET /` – Health Check

Returns JSON with:
```json
{
  "status": "running",
  "bot": "Construction SaaS WhatsApp Bot",
  "assistant_id": "asst_...",
  "webhook": "https://<ngrok-url>/webhook"
}
```

### `POST /webhook` – Twilio WhatsApp Webhook

The main entry point for all incoming WhatsApp messages. Accepts form data from Twilio and routes to the appropriate handler based on message type.

**Request:** Twilio form-encoded POST
**Response:** TwiML XML (`application/xml`)

### `GET /media/{filename}` – Media Server

Serves temporary audio files (TTS output) so Twilio can fetch and deliver them to the user. Files are stored in the `temp_media/` directory.

---

## ⚙️ Environment Configuration

The server requires a `.env` file with the following variables:

| Variable              | Description                                |
|-----------------------|--------------------------------------------|
| `OPENAI_API_KEY`      | OpenAI API key for all AI services         |
| `TWILIO_ACCOUNT_SID`  | Twilio Account SID for authentication      |
| `TWILIO_AUTH_TOKEN`    | Twilio Auth Token for API access           |
| `OPENAI_ASSISTANT_ID` | The pre-configured OpenAI Assistant ID     |

---

## 📁 Directory Structure

```
construction saas/
├── main.py              # Main application – all server logic
├── .env                 # Environment variables (API keys)
├── .gitignore           # Git ignore rules
├── requirements.txt     # Python dependencies
├── architecture.md      # This documentation file
└── temp_media/          # Temporary directory for downloaded/generated media
    ├── *.pdf            # Downloaded PDFs (cleaned up after processing)
    ├── *.ogg            # Downloaded voice notes (cleaned up after processing)
    └── tts_*.mp3        # Generated TTS audio (kept for Twilio to fetch)
```

---

## 🔐 Security Notes

- **API keys** are stored in `.env` and loaded at runtime (never hardcoded in source)
- **Twilio media downloads** use authenticated HTTP requests (`TWILIO_SID` + `TWILIO_TOKEN`)
- **ngrok authtoken** is configured for secure tunneling
- **Temporary media files** are cleaned up after processing to minimize disk usage

---

> **Built with ❤️ for the Construction Industry**
