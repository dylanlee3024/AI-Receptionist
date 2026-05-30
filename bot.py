import os
import asyncio
import time

from dotenv import load_dotenv
from fastapi import WebSocket
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import FunctionCallResultProperties, TTSSpeakFrame
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_mute import MuteUntilFirstBotCompleteUserMuteStrategy
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService, ElevenLabsTTSSettings, TextAggregationMode
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from call_control import END_CALL_MESSAGE, estimate_hangup_delay_seconds
from lead_handoff import send_lead_handoff
from script_config import GREETING, build_system_prompt

load_dotenv()

DEFAULT_ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Sarah - mature, reassuring, confident.
DEFAULT_ELEVENLABS_MODEL = "eleven_turbo_v2_5"
DEFAULT_ELEVENLABS_SPEED = 1.0
DEFAULT_ELEVENLABS_STABILITY = 0.82
DEFAULT_ELEVENLABS_SIMILARITY_BOOST = 0.75
DEFAULT_ELEVENLABS_STYLE = 0.0
DEFAULT_ELEVENLABS_USE_SPEAKER_BOOST = False
DEFAULT_ELEVENLABS_APPLY_TEXT_NORMALIZATION = "auto"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_MAX_TOKENS = 90
DEFAULT_OPENAI_TEMPERATURE = 0.2
DEFAULT_DEEPGRAM_ENDPOINTING_MS = 100
DEFAULT_AUDIO_OUT_SAMPLE_RATE = 16000
DEFAULT_AUDIO_OUT_10MS_CHUNKS = 2

END_CALL_TOOL = FunctionSchema(
    name="end_call",
    description=(
        "End the active phone call after the conversation is complete, the caller asks "
        "to hang up, or the caller has no more service-related questions."
    ),
    properties={
        "reason": {
            "type": "string",
            "description": "Brief reason for ending the call.",
        }
    },
    required=["reason"],
)

# Pre-load both VAD and Smart Turn models at startup so the first call doesn't pay the loading cost.
_vad_analyzer = SileroVADAnalyzer()
_smart_turn = LocalSmartTurnAnalyzerV3()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for {}={!r}; using {}", name, value, default)
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for {}={!r}; using {}", name, value, default)
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid boolean for {}={!r}; using {}", name, value, default)
    return default


def _env_int_range(name: str, default: int, minimum: int, maximum: int) -> int:
    value = _env_int(name, default)
    if minimum <= value <= maximum:
        return value
    logger.warning("Invalid range for {}={}; using {}", name, value, default)
    return default


def _env_float_range(name: str, default: float, minimum: float, maximum: float) -> float:
    value = _env_float(name, default)
    if minimum <= value <= maximum:
        return value
    logger.warning("Invalid range for {}={}; using {}", name, value, default)
    return default


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in choices:
        return normalized
    logger.warning("Invalid choice for {}={!r}; using {}", name, value, default)
    return default



async def run_bot(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    caller_number: str | None = None,
    twilio_number: str | None = None,
):
    call_started_at = time.monotonic()
    last_tts_request_at: float | None = None
    system_prompt = build_system_prompt(caller_number)
    # Seed context with the greeting so the LLM always has proper conversation history,
    # even if the caller interrupts before the greeting finishes playing.
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "assistant", "content": GREETING},
    ]

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
    )

    audio_out_sample_rate = _env_int_range(
        "AUDIO_OUT_SAMPLE_RATE",
        DEFAULT_AUDIO_OUT_SAMPLE_RATE,
        8000,
        48000,
    )
    audio_out_10ms_chunks = _env_int_range(
        "AUDIO_OUT_10MS_CHUNKS",
        DEFAULT_AUDIO_OUT_10MS_CHUNKS,
        1,
        10,
    )
    logger.info(
        "Using audio output sample_rate={} chunk_ms={}",
        audio_out_sample_rate,
        audio_out_10ms_chunks * 10,
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=audio_out_sample_rate,
            audio_out_10ms_chunks=audio_out_10ms_chunks,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    deepgram_model = os.getenv("DEEPGRAM_MODEL", "nova-3")
    deepgram_endpointing_ms = _env_int("DEEPGRAM_ENDPOINTING_MS", DEFAULT_DEEPGRAM_ENDPOINTING_MS)
    logger.info(
        "Using Deepgram STT model={} endpointing_ms={} smart_format=True",
        deepgram_model,
        deepgram_endpointing_ms,
    )
    stt = DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        settings=DeepgramSTTService.Settings(
            model=deepgram_model,
            language="en",
            smart_format=True,
            endpointing=deepgram_endpointing_ms,
        ),
    )

    openai_model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    openai_temperature = _env_float("OPENAI_TEMPERATURE", DEFAULT_OPENAI_TEMPERATURE)
    openai_max_tokens = _env_int("OPENAI_MAX_TOKENS", DEFAULT_OPENAI_MAX_TOKENS)
    logger.info(
        "Using OpenAI LLM model={} temperature={} max_tokens={}",
        openai_model,
        openai_temperature,
        openai_max_tokens,
    )
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            model=openai_model,
            temperature=openai_temperature,
            max_tokens=openai_max_tokens,
        ),
    )
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID)
    tts_model = os.getenv("ELEVENLABS_MODEL", DEFAULT_ELEVENLABS_MODEL)
    tts_speed = _env_float_range("ELEVENLABS_SPEED", DEFAULT_ELEVENLABS_SPEED, 0.7, 1.2)
    tts_stability = _env_float_range(
        "ELEVENLABS_STABILITY",
        DEFAULT_ELEVENLABS_STABILITY,
        0.0,
        1.0,
    )
    tts_similarity_boost = _env_float_range(
        "ELEVENLABS_SIMILARITY_BOOST",
        DEFAULT_ELEVENLABS_SIMILARITY_BOOST,
        0.0,
        1.0,
    )
    tts_style = _env_float_range("ELEVENLABS_STYLE", DEFAULT_ELEVENLABS_STYLE, 0.0, 1.0)
    tts_use_speaker_boost = _env_bool(
        "ELEVENLABS_USE_SPEAKER_BOOST",
        DEFAULT_ELEVENLABS_USE_SPEAKER_BOOST,
    )
    tts_apply_text_normalization = _env_choice(
        "ELEVENLABS_APPLY_TEXT_NORMALIZATION",
        DEFAULT_ELEVENLABS_APPLY_TEXT_NORMALIZATION,
        {"auto", "on", "off"},
    )
    logger.info(
        (
            "Using ElevenLabs WS TTS voice_id={} model={} sample_rate={} "
            "speed={} stability={} similarity_boost={} "
            "style={} speaker_boost={} text_normalization={}"
        ),
        voice_id,
        tts_model,
        audio_out_sample_rate,
        tts_speed,
        tts_stability,
        tts_similarity_boost,
        tts_style,
        tts_use_speaker_boost,
        tts_apply_text_normalization,
    )

    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        text_aggregation_mode=TextAggregationMode.TOKEN,
        settings=ElevenLabsTTSSettings(
            voice=voice_id,
            model=tts_model,
            speed=tts_speed,
            stability=tts_stability,
            similarity_boost=tts_similarity_boost,
            style=tts_style,
            use_speaker_boost=tts_use_speaker_boost,
            apply_text_normalization=tts_apply_text_normalization,
        ),
    )

    @tts.event_handler("on_tts_request")
    async def on_tts_request(tts, context_id, text):
        nonlocal last_tts_request_at
        now = time.monotonic()
        elapsed_ms = (now - call_started_at) * 1000
        gap_ms = (now - last_tts_request_at) * 1000 if last_tts_request_at else None
        last_tts_request_at = now
        logger.info(
            "ElevenLabs HTTP TTS request context_id={} chars={} elapsed_ms={:.0f} gap_ms={} text={!r}",
            context_id,
            len(text),
            elapsed_ms,
            f"{gap_ms:.0f}" if gap_ms is not None else "first",
            text[:120],
        )

    context = LLMContext(messages, tools=ToolsSchema([END_CALL_TOOL]))
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=_smart_turn)]
            ),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )
    user_aggregator = context_aggregator.user()
    assistant_aggregator = context_aggregator.assistant()

    pipeline = Pipeline([
        transport.input(),
        VADProcessor(vad_analyzer=_vad_analyzer),
        stt,
        user_aggregator,
        llm,
        tts,
        assistant_aggregator,
        transport.output(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_out_sample_rate=audio_out_sample_rate,
        ),
        enable_rtvi=False,
    )
    hangup_requested = False

    async def complete_twilio_call_after_delay(reason: str, delay: float | None = None):
        delay = estimate_hangup_delay_seconds() if delay is None else delay
        logger.info("Completing Twilio call after {}s reason={}", delay, reason)
        await asyncio.sleep(delay)

        try:
            from twilio.rest import Client

            client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            await asyncio.to_thread(client.calls(call_sid).update, status="completed")
            logger.info("Twilio call completed call_sid={} reason={}", call_sid, reason)
        except Exception as exc:
            logger.exception("Failed to complete Twilio call via REST: {}", exc)
            if not task.has_finished():
                await task.cancel(reason=f"end_call fallback: {reason}")

    async def handle_end_call(params: FunctionCallParams):
        nonlocal hangup_requested
        reason = str(params.arguments.get("reason") or "conversation complete")

        if hangup_requested:
            await params.result_callback(
                {"status": "already_ending"},
                properties=FunctionCallResultProperties(run_llm=False),
            )
            return

        hangup_requested = True
        logger.info("LLM requested end_call reason={}", reason)
        await task.queue_frames([TTSSpeakFrame(END_CALL_MESSAGE)])
        asyncio.create_task(complete_twilio_call_after_delay(reason))
        await params.result_callback(
            {"status": "ending"},
            properties=FunctionCallResultProperties(run_llm=False),
        )

    llm.register_function("end_call", handle_end_call)

    @task.event_handler("on_pipeline_error")
    async def on_pipeline_error(task, frame):
        logger.error(
            "Pipeline error: {} fatal={} exception={}",
            frame.error,
            frame.fatal,
            frame.exception,
        )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — playing greeting via TTS")
        await task.queue_frames([TTSSpeakFrame(GREETING)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected — cancelling pipeline task")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)
    await send_lead_handoff(
        messages=context.messages,
        call_sid=call_sid,
        stream_sid=stream_sid,
        caller_number=caller_number,
        twilio_number=twilio_number,
    )


