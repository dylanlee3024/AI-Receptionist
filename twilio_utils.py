from urllib.parse import parse_qs


def twilio_form_value(body: bytes, key: str) -> str:
    form = parse_qs(body.decode())
    return form.get(key, [""])[0]


def caller_number_from_twilio_body(body: bytes) -> str:
    return twilio_form_value(body, "From")


def twilio_number_from_twilio_body(body: bytes) -> str:
    return twilio_form_value(body, "To")
