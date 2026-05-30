# Receptionist Voice Agent

An AI phone receptionist for a plumbing company, built on pipecat and deployed to Fly.io. Incoming Twilio calls are handled end-to-end in real time: the agent greets the caller, collects their name, service address, and issue description, then confirms a plumber will call back within two hours.

---

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
  │  FastAPIWebsocketTransport (input)       │  ← receives audio frames from Twilio
  │  SileroVADAnalyzer                       │  ← detects speech / silence
  │  DeepgramSTTService  (nova-3)            │  ← speech → text
  │  OpenAILLMContext aggregator (user)      │  ← buffers user turn
  │  OpenAILLMService   (gpt-4o-mini)        │  ← generates response text
  │  ElevenLabsTTSService (flash v2.5)       │  ← text → audio
  │  OpenAILLMContext aggregator (assistant) │  ← records assistant turn
  │  FastAPIWebsocketTransport (output)      │  ← sends audio back to Twilio
  └─────────────────────────────────────────┘
```

### Key files

| File | Role |
|------|------|
| `main.py` | FastAPI app; handles the `/incoming-call` TwiML webhook and `/ws` WebSocket endpoint |
| `bot.py` | Builds and runs the pipecat pipeline for each call |
| `Dockerfile` | Python 3.11-slim image with `libgomp1` for Silero VAD |
| `fly.toml` | Fly.io deployment config (app: `cph`, region: `iad`) |
| `requirements.txt` | Pinned to `pipecat-ai==1.2.1` |

---

## Call flow

1. Twilio receives the PSTN call and POSTs to `/incoming-call`.
2. The server returns TwiML that opens a Media Stream WebSocket to `/ws`.
3. `main.py` waits for the Twilio `start` event to extract `streamSid` and `callSid`, then hands off to `run_bot()`.
4. `run_bot()` builds the pipeline and registers event handlers.
5. On `on_client_connected`, an `LLMContextFrame` is queued — this immediately triggers the LLM to generate the opening greeting without waiting for the caller to speak first.
6. From that point the pipeline runs in a loop: VAD detects the caller speaking → Deepgram transcribes → LLM responds → ElevenLabs synthesizes → audio is streamed back.
7. On `on_client_disconnected`, the pipeline task is cancelled cleanly.

---

## Primary goal: low latency

Every service in the stack is chosen for speed, not just quality.

| Stage | Choice | Why |
|-------|--------|-----|
| STT | Deepgram nova-3 | Streaming transcription with <300 ms first-word latency |
| LLM | gpt-4o-mini | Fast time-to-first-token; short system prompt keeps context small |
| TTS | ElevenLabs `eleven_flash_v2_5` | Optimised for real-time streaming; lower latency than standard models |
| VAD | Silero (local, on-device) | No extra network hop; instant end-of-speech detection |
| Transport | Twilio Media Streams over WebSocket | Low-overhead bidirectional binary audio |
| Infrastructure | Fly.io `iad` (US East) | Co-located with Twilio's US edge for minimal round-trip |

Additional latency considerations in the code:
- `allow_interruptions=True` in `PipelineParams` lets the caller cut off the agent mid-sentence without waiting for TTS to finish.
- `AUDIO_OUT_SAMPLE_RATE` defaults to `16000` for smoother TTS generation; the Twilio serializer converts output to 8 kHz mulaw.
- `force_gc=True` on `PipelineRunner` reduces GC pauses during live calls.

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TWILIO_ACCOUNT_SID` | Twilio account identifier |
| `TWILIO_AUTH_TOKEN` | Twilio auth token (used by the Twilio serializer) |
| `DEEPGRAM_API_KEY` | Deepgram STT |
| `DEEPGRAM_MODEL` | Optional; defaults to `nova-3` |
| `DEEPGRAM_ENDPOINTING_MS` | Optional; defaults to `150` for faster final transcripts |
| `OPENAI_API_KEY` | OpenAI LLM |
| `OPENAI_MODEL` | Optional; defaults to `gpt-4o-mini` |
| `OPENAI_TEMPERATURE` | Optional; defaults to `0.2` |
| `OPENAI_MAX_TOKENS` | Optional; defaults to `90` to keep replies concise |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice (defaults to Rachel `21m00Tcm4TlvDq8ikWAM`) |
| `ELEVENLABS_SPEED` | Optional; defaults to `1.0` for natural cadence |
| `ELEVENLABS_STABILITY` | Optional; defaults to `0.82` to reduce cadence glitches |
| `ELEVENLABS_SIMILARITY_BOOST` | Optional; defaults to `0.75` to keep voice identity consistent without overfitting artifacts |
| `ELEVENLABS_STYLE` | Optional; defaults to `0.0` for a calmer receptionist tone |
| `ELEVENLABS_USE_SPEAKER_BOOST` | Optional; defaults to `false` to avoid extra processing and level changes |
| `ELEVENLABS_APPLY_TEXT_NORMALIZATION` | Optional; defaults to `auto` |
| `AUDIO_OUT_SAMPLE_RATE` | Optional; defaults to `16000`; Twilio output is converted to 8 kHz mulaw by the serializer |
| `AUDIO_OUT_10MS_CHUNKS` | Optional; defaults to `4` for 40 ms output chunks |
| `RESEND_API_KEY` | Resend email delivery for post-call lead summaries |
| `LEAD_EMAIL_FROM` | Lead email sender, defaults to `Campbell's Receptionist <hello@hyalitedigital.com>` |
| `LEAD_EMAIL_TO` | Lead email recipients, comma-separated |
| `TWILIO_SMS_FROM` | Optional SMS sender override; defaults to the Twilio number that received the call |
| `FOLLOWUP_SMS_ENABLED` | Optional; set `false` to disable post-call customer SMS |

Set these as Fly secrets: `fly secrets set KEY=value -a cph`

---

## Deploying

```bash
fly deploy -a cph
fly logs -a cph      # watch for startup errors
```
