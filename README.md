# Receptionist

> AI voice receptionist for service-based trades companies — answers the phone, reassures callers, and captures lead details so the team can call back informed.

## Overview

Receptionist is a real-time AI phone agent built on [pipecat](https://github.com/pipecat-ai/pipecat) and deployed to Fly.io. When a customer calls, Twilio routes the call to a FastAPI WebSocket server running a full speech-to-text → LLM → text-to-speech pipeline. The agent greets the caller, works through a structured intake (name, issue, address, callback number), and the moment the call ends it uses a second LLM pass to extract a structured lead summary — then fires off a notification email and a follow-up SMS to the customer automatically. I built and deployed this at my current company.

## Tech Stack

- **pipecat-ai 1.2.1** — real-time voice pipeline framework
- **Deepgram nova-3** — streaming speech-to-text
- **OpenAI gpt-4o-mini** — LLM response generation
- **ElevenLabs eleven_turbo_v2_5** — low-latency text-to-speech
- **Silero VAD** — local on-device voice activity detection
- **Twilio Media Streams** — PSTN telephony + WebSocket audio transport
- **FastAPI + uvicorn** — WebSocket server
- **Resend** — transactional email for post-call lead summaries
- **Fly.io** — deployment (performance-1x machines, 2 GB RAM)
- **Docker** — containerized with Python 3.11-slim

## Architecture

```
Twilio PSTN call
      │
      │  TwiML redirect → wss://<host>/ws
      ▼
FastAPI WebSocket (/ws)          [main.py]
      │
      │  Twilio Media Streams (mulaw 8 kHz, base64)
      ▼
pipecat pipeline                 [bot.py]
  ┌─────────────────────────────────────────┐
  │  FastAPIWebsocketTransport (input)       │
  │  SileroVADAnalyzer + SmartTurnAnalyzer   │
  │  DeepgramSTTService  (nova-3)            │
  │  OpenAILLMService    (gpt-4o-mini)       │
  │  ElevenLabsTTSService (turbo v2.5)       │
  │  FastAPIWebsocketTransport (output)      │
  └─────────────────────────────────────────┘
      │
      ▼  (after call ends)
Lead extraction (gpt-4o-mini) → Resend email + Twilio SMS
```

## Getting Started

### Prerequisites

- Python 3.11+
- A [Twilio](https://twilio.com) account with a phone number configured for Media Streams
- Paid API accounts for [OpenAI](https://platform.openai.com), [Deepgram](https://deepgram.com), [ElevenLabs](https://elevenlabs.io), and [Resend](https://resend.com)

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/Receptionist.git
cd Receptionist

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### Running locally

```bash
cp .env.example .env
# Fill in your API keys (see Required Configuration below)

uvicorn main:app --host 0.0.0.0 --port 8080
```

Expose port 8080 to the internet (e.g. via [ngrok](https://ngrok.com)) and point your Twilio number's webhook at `https://<your-host>/incoming-call`.

### Deploying to Fly.io

```bash
fly launch          # first time only
fly secrets set \
  OPENAI_API_KEY=sk-... \
  TWILIO_ACCOUNT_SID=AC... \
  TWILIO_AUTH_TOKEN=... \
  DEEPGRAM_API_KEY=... \
  ELEVENLABS_API_KEY=sk_... \
  RESEND_API_KEY=re_...
fly deploy
```

### Required Configuration

Copy `.env.example` to `.env` and supply values for every key:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | LLM responses and post-call lead extraction |
| `TWILIO_ACCOUNT_SID` | Twilio account identifier |
| `TWILIO_AUTH_TOKEN` | Twilio auth (used by the serializer to end calls via REST) |
| `DEEPGRAM_API_KEY` | Streaming speech-to-text |
| `ELEVENLABS_API_KEY` | Text-to-speech synthesis |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice to use |
| `RESEND_API_KEY` | Post-call lead summary email delivery |
| `LEAD_EMAIL_TO` | Recipient address(es) for lead emails |
| `TWILIO_SMS_FROM` | Twilio number to send follow-up SMS from |

All other variables in `.env.example` are optional tuning knobs with sensible defaults.

> ⚠️ Note: `.env` and all credentials have been excluded from this repository. You must supply your own API keys and a configured Twilio phone number to run the project.

## Engineering Notes

### Challenges

**Latency was the central problem.** Voice conversations break down the moment there's a noticeable delay — anything over ~1 second feels broken on a phone call. The pipeline has several moving parts (VAD, STT, LLM, TTS) each adding latency in series, so every stage had to be squeezed.

**Audio end-of-speech detection** was particularly tricky. Early versions would cut the caller off mid-sentence because the VAD triggered too aggressively on short pauses. The fix was combining Silero VAD (for coarse speech/silence detection) with pipecat's `LocalSmartTurnAnalyzerV3` — a model that understands whether a sentence is grammatically complete before signaling end-of-turn. Both models are pre-loaded at server startup so the first call doesn't pay a cold-start penalty.

**Model warm-up** was also a factor — lazy initialization on the first call added several hundred milliseconds. Preloading `SileroVADAnalyzer` and `LocalSmartTurnAnalyzerV3` as module-level globals (see `bot.py`) eliminated this.

### Breakthroughs

The biggest architectural shift was **abandoning OpenAI's Realtime API** in favor of a pipecat pipeline with best-in-class providers for each stage. The Realtime API is convenient but not fast enough for telephone-quality UX. Routing through Deepgram (STT) + gpt-4o-mini (LLM) + ElevenLabs Turbo (TTS) separately gave more control over each latency budget and produced noticeably snappier responses.

Upgrading the Fly.io machines from shared to **performance-1x (2 GB RAM)** also made a meaningful difference — the CPU-bound Silero VAD model runs much faster with dedicated cores.

## Screenshots

<!-- Add screenshots or a demo recording here -->

## License

MIT
