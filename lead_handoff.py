import asyncio
import json
import os
import re
from html import escape
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:
    import logging

    logger = logging.getLogger(__name__)

from openai import AsyncOpenAI

from company_knowledge import COMPANY_NAME

HANDOFF_MODEL = os.getenv("OPENAI_HANDOFF_MODEL", "gpt-4o-mini")
_openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DEFAULT_LEAD_EMAIL_FROM = "Campbell's Receptionist <hello@hyalitedigital.com>"
DEFAULT_LEAD_EMAIL_TO = "dylanlee3024@gmail.com"


def transcript_from_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"assistant", "user"} or not isinstance(content, str):
            continue
        if not content.strip():
            continue
        lines.append(f"{role}: {content.strip()}")
    return "\n".join(lines)


async def extract_lead_summary(
    *,
    messages: list[dict[str, Any]],
    call_sid: str,
    stream_sid: str,
    caller_number: str | None,
) -> dict[str, Any]:
    transcript = transcript_from_messages(messages)
    if not transcript:
        return {
            "call_sid": call_sid,
            "stream_sid": stream_sid,
            "caller_number": caller_number,
            "call_type": "unknown",
            "summary": "No transcript was captured.",
            "transcript": "",
        }

    response = await _openai_client.chat.completions.create(
        model=HANDOFF_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract a concise JSON lead summary for Campbell's Plumbing & Heating. "
                    "Use null for missing values. Required keys: call_type, name, "
                    "callback_number, callback_number_confirmed, problem_or_request, problem_details, "
                    "service_address, is_homeowner, email, equipment_type, equipment_age, "
                    "home_age, has_worked_with_campbells_before, next_step, summary. "
                    "Use true, false, or null for has_worked_with_campbells_before. "
                    "If the caller confirms the Twilio caller "
                    "ID as the callback number, set callback_number to that caller ID. "
                    "Make problem_or_request a short customer-friendly phrase suitable for "
                    "a follow-up SMS or subject line, such as leaking faucet or no heat from furnace. "
                    "Make problem_details a single detailed phrase for the service team. Include "
                    "all useful issue details from the transcript: source or location, age of "
                    "equipment or fixture, whether it happens constantly or only when used, "
                    "symptoms, system type, severity, and any other follow-up answers. Do not "
                    "drop details just because problem_or_request is short. Example: leak from "
                    "10 year old faucet underneath only when water runs."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Call SID: {call_sid}\n"
                    f"Stream SID: {stream_sid}\n"
                    f"Caller ID from Twilio: {caller_number or 'unknown'}\n\n"
                    f"Transcript:\n{transcript}"
                ),
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    try:
        summary = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Lead summary was not valid JSON: {}", content)
        summary = {"summary": content}

    summary.setdefault("call_sid", call_sid)
    summary.setdefault("stream_sid", stream_sid)
    summary.setdefault("caller_number", caller_number)
    summary["transcript"] = transcript
    return summary


def normalize_phone_number(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None

    value = value.strip()
    digits = re.sub(r"\D", "", value)
    if value.startswith("+") and len(digits) >= 10:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def _clean_issue(value: Any, *, max_length: int = 90) -> str:
    if not isinstance(value, str):
        return ""
    issue = " ".join(value.strip().split())
    issue = re.sub(
        r"^(i need|i have|i am having|i'm having|my|a|an)\s+",
        "",
        issue,
        flags=re.IGNORECASE,
    )
    issue = issue.rstrip(".!?")
    if len(issue) > max_length:
        issue = issue[: max_length - 3].rstrip() + "..."
    return issue


def sms_issue(summary: dict[str, Any]) -> str:
    for key in ("problem_or_request", "equipment_type", "summary"):
        issue = _clean_issue(summary.get(key))
        if issue:
            return issue
    return "your request"


def detailed_problem(summary: dict[str, Any]) -> str:
    for key in ("problem_details", "problem_or_request", "summary"):
        issue = _clean_issue(summary.get(key), max_length=220)
        if issue:
            return issue
    return "Not provided"


def build_followup_sms_body(summary: dict[str, Any]) -> str:
    issue = sms_issue(summary)
    if issue == "your request":
        return (
            "Thanks for taking the time to give us your information. Our team has been "
            "notified, and a professional will reach out shortly."
        )
    return (
        "Thanks for taking the time to give us your information. Our team has been "
        f"notified of your request about {issue}, and a professional will reach out shortly."
    )


def _sms_recipient(summary: dict[str, Any], caller_number: str | None) -> str | None:
    return normalize_phone_number(summary.get("callback_number")) or normalize_phone_number(
        caller_number
    )


def _sms_sender(twilio_number: str | None) -> str | None:
    return normalize_phone_number(os.getenv("TWILIO_SMS_FROM")) or normalize_phone_number(
        twilio_number
    )


def _followup_sms_enabled() -> bool:
    return os.getenv("FOLLOWUP_SMS_ENABLED", "true").lower() not in {"0", "false", "no"}


def _send_followup_sms_sync(
    summary: dict[str, Any],
    *,
    caller_number: str | None,
    twilio_number: str | None,
):
    if not _followup_sms_enabled():
        logger.info("Follow-up SMS disabled by FOLLOWUP_SMS_ENABLED.")
        return

    to_number = _sms_recipient(summary, caller_number)
    from_number = _sms_sender(twilio_number)
    if not to_number:
        logger.warning("Follow-up SMS skipped; no callback phone number found.")
        return
    if not from_number:
        logger.warning("Follow-up SMS skipped; no Twilio SMS from number configured.")
        return

    from twilio.rest import Client

    client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    message = client.messages.create(
        body=build_followup_sms_body(summary),
        from_=from_number,
        to=to_number,
    )
    logger.info("Follow-up SMS sent sid={} to={}", message.sid, to_number)


def _lead_email_recipients() -> list[str]:
    email_to = os.getenv("LEAD_EMAIL_TO", DEFAULT_LEAD_EMAIL_TO)
    return [recipient.strip() for recipient in email_to.split(",") if recipient.strip()]


def _lead_name(summary: dict[str, Any]) -> str:
    name = summary.get("name")
    if isinstance(name, str) and name.strip():
        return " ".join(name.strip().split())

    parts = []
    for key in ("first_name", "last_name"):
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(parts) or "Unknown caller"


def _lead_phone(summary: dict[str, Any]) -> str:
    phone = normalize_phone_number(summary.get("callback_number")) or normalize_phone_number(
        summary.get("caller_number")
    )
    return phone or "Not provided"


def _lead_field(summary: dict[str, Any], key: str) -> str:
    value = summary.get(key)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str) and value.strip():
        return " ".join(value.strip().split())
    return "Not provided"


def build_lead_email_subject(summary: dict[str, Any]) -> str:
    return f"Service Request - {_lead_name(summary)} - {sms_issue(summary)}"


def build_lead_email_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Name: {_lead_name(summary)}",
            f"Phone: {_lead_phone(summary)}",
            f"Address: {_lead_field(summary, 'service_address')}",
            f"Email: {_lead_field(summary, 'email')}",
            f"Existing Customer: {_lead_field(summary, 'has_worked_with_campbells_before')}",
            f"Problem: {detailed_problem(summary)}",
        ]
    )


def build_resend_email_payload(summary: dict[str, Any]) -> dict[str, Any]:
    subject = build_lead_email_subject(summary)
    fields = [
        ("Name", _lead_name(summary)),
        ("Phone", _lead_phone(summary)),
        ("Address", _lead_field(summary, "service_address")),
        ("Email", _lead_field(summary, "email")),
        ("Existing Customer", _lead_field(summary, "has_worked_with_campbells_before")),
        ("Problem", detailed_problem(summary)),
    ]
    rows = "".join(
        "<tr>"
        f"<td style=\"padding: 6px 12px 6px 0;\"><strong>{escape(label)}:</strong></td>"
        f"<td style=\"padding: 6px 0;\">{escape(value)}</td>"
        "</tr>"
        for label, value in fields
    )
    html = (
        "<table style=\"border-collapse: collapse; font-family: Arial, sans-serif; "
        "font-size: 15px; line-height: 1.4;\">"
        f"{rows}"
        "</table>"
    )
    return {
        "from": os.getenv("LEAD_EMAIL_FROM", DEFAULT_LEAD_EMAIL_FROM),
        "to": _lead_email_recipients(),
        "subject": subject,
        "html": html,
        "text": build_lead_email_text(summary),
    }


def _send_resend_email_sync(summary: dict[str, Any]):
    if not os.getenv("RESEND_API_KEY"):
        logger.warning("Resend lead email not configured; lead summary logged only.")
        return

    import resend

    resend.api_key = os.environ["RESEND_API_KEY"]
    response = resend.Emails.send(build_resend_email_payload(summary))
    logger.info("Lead summary email sent with Resend: {}", response)


async def send_lead_handoff(
    *,
    messages: list[dict[str, Any]],
    call_sid: str,
    stream_sid: str,
    caller_number: str | None,
    twilio_number: str | None = None,
):
    try:
        summary = await extract_lead_summary(
            messages=messages,
            call_sid=call_sid,
            stream_sid=stream_sid,
            caller_number=caller_number,
        )
    except Exception as exc:
        logger.exception("Lead extraction failed: {}", exc)
        return

    summary.setdefault("twilio_number", twilio_number)
    logger.info("Lead summary extracted: {}", json.dumps(summary, ensure_ascii=False))

    results = await asyncio.gather(
        asyncio.to_thread(_send_resend_email_sync, summary),
        asyncio.to_thread(
            _send_followup_sms_sync,
            summary,
            caller_number=caller_number,
            twilio_number=twilio_number,
        ),
        return_exceptions=True,
    )
    labels = ("Lead summary email", "Follow-up SMS")
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            logger.exception("{} failed: {}", label, result)
