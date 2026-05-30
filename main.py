from html import escape
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from loguru import logger

from bot import run_bot
from twilio_utils import caller_number_from_twilio_body, twilio_number_from_twilio_body

load_dotenv()


app = FastAPI()


@app.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.headers.get("host")
    body = await request.body()
    caller_number = caller_number_from_twilio_body(body)
    twilio_number = twilio_number_from_twilio_body(body)
    logger.info(
        "Incoming call from {} to {}",
        caller_number or "unknown",
        twilio_number or "unknown",
    )
    caller_number_xml = escape(caller_number, quote=True)
    twilio_number_xml = escape(twilio_number, quote=True)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/ws">
            <Parameter name="caller_number" value="{caller_number_xml}" />
            <Parameter name="twilio_number" value="{twilio_number_xml}" />
        </Stream>
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    stream_sid = None
    call_sid = None
    caller_number = None
    twilio_number = None

    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        event = msg.get("event")

        if event == "connected":
            logger.info("Twilio connected event received")
            continue

        if event == "start":
            stream_sid = msg["start"]["streamSid"]
            call_sid = msg["start"]["callSid"]
            custom_parameters = msg["start"].get("customParameters") or {}
            caller_number = custom_parameters.get("caller_number")
            twilio_number = custom_parameters.get("twilio_number")
            logger.info(
                "Stream started — stream_sid={} call_sid={} caller_number={} twilio_number={}",
                stream_sid,
                call_sid,
                caller_number or "unknown",
                twilio_number or "unknown",
            )
            break

    if not stream_sid:
        logger.error("Never received Twilio start event; closing websocket")
        await websocket.close()
        return

    await run_bot(
        websocket,
        stream_sid,
        call_sid,
        caller_number=caller_number,
        twilio_number=twilio_number,
    )
