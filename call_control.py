END_CALL_MESSAGE = (
    "I'm sending our dispatcher your information now; they will be in touch with you shortly. Have a great day!"
)


def estimate_hangup_delay_seconds(message: str = END_CALL_MESSAGE) -> float:
    # Give Twilio enough time to play a short TTS goodbye before completing the call.
    return min(8.0, max(4.0, len(message) / 12.0))
