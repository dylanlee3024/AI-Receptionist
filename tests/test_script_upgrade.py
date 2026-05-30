import unittest

from call_control import END_CALL_MESSAGE, estimate_hangup_delay_seconds
from company_knowledge import get_company_info
from lead_handoff import (
    build_followup_sms_body,
    build_lead_email_subject,
    build_lead_email_text,
    build_resend_email_payload,
    normalize_phone_number,
    transcript_from_messages,
)
from script_config import GREETING, build_system_prompt
from twilio_utils import caller_number_from_twilio_body, twilio_number_from_twilio_body


class ScriptUpgradeTests(unittest.TestCase):
    def test_prompt_includes_caller_id_and_paths(self):
        prompt = build_system_prompt("+14065551212")

        self.assertNotIn("+14065551212", prompt)
        self.assertIn("number they are calling from", prompt)
        self.assertIn("Never read, repeat, or confirm the digits", prompt)
        self.assertIn("demand_service", prompt)
        self.assertIn("other", prompt)
        self.assertIn("homeowner", prompt)
        self.assertIn("worked with Campbell's before", prompt)
        self.assertIn("first and last name", prompt)

    def test_prompt_includes_guardrails_and_call_control(self):
        prompt = build_system_prompt("+14065551212")

        self.assertIn("Do not answer unrelated questions", prompt)
        self.assertIn("coding questions", prompt)
        self.assertIn("only say you are a virtual assistant", prompt)
        self.assertIn("Use end_call only", prompt)

    def test_prompt_matches_caller_flow(self):
        prompt = build_system_prompt("+14065551212")

        self.assertIn("Match the caller's flow", prompt)
        self.assertIn("do not ask for them again", prompt)
        self.assertIn("If they lead with the issue", prompt)
        self.assertIn("Do not make standalone filler responses", prompt)
        self.assertIn("Avoid exclamation points", prompt)
        self.assertIn("TTS-friendly", prompt)

    def test_prompt_includes_scheduling_reassurance(self):
        prompt = build_system_prompt("+14065551212")

        self.assertIn("same-day service", prompt)
        self.assertIn("live scheduling", prompt)
        self.assertIn("next few minutes", prompt)

    def test_prompt_includes_diagnostic_discovery(self):
        prompt = build_system_prompt("+14065551212")

        self.assertIn("empathy", prompt)
        self.assertIn("Always ask the age", prompt)
        self.assertIn("leaks constantly", prompt)
        self.assertIn("blows cold air", prompt)
        self.assertIn("do not diagnose", prompt)

    def test_greeting_identifies_rachel_and_campbells(self):
        self.assertIn("Rachel", GREETING)
        self.assertIn("Campbell's", GREETING)
        self.assertIn("virtual assistant", GREETING)

    def test_twilio_from_number_extraction(self):
        body = b"CallSid=CA123&From=%2B14065551212&To=%2B14065550000"

        self.assertEqual(caller_number_from_twilio_body(body), "+14065551212")
        self.assertEqual(twilio_number_from_twilio_body(body), "+14065550000")

    def test_transcript_filters_system_messages(self):
        transcript = transcript_from_messages(
            [
                {"role": "system", "content": "rules"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "Need service"},
            ]
        )

        self.assertEqual(transcript, "assistant: Hi\nuser: Need service")

    def test_resend_email_payload_subject(self):
        payload = build_resend_email_payload(
            {
                "name": "Dylan Lee",
                "callback_number": "+14065551212",
                "service_address": "123 Main St",
                "email": "dylan@example.com",
                "has_worked_with_campbells_before": "yes",
                "problem_or_request": "leaking faucet",
                "problem_details": "leak from 10 year old faucet underneath only when water runs",
            }
        )

        self.assertEqual(payload["to"], ["dylanlee3024@gmail.com"])
        self.assertEqual(payload["subject"], "Service Request - Dylan Lee - leaking faucet")
        self.assertIn("Name:", payload["text"])
        self.assertIn("Phone:", payload["text"])
        self.assertIn("Existing Customer:", payload["text"])
        self.assertNotIn("transcript", payload["html"].lower())

    def test_lead_email_contains_only_requested_fields(self):
        summary = {
            "name": "Dylan Lee",
            "callback_number": "+14065551212",
            "service_address": "123 Main St",
            "email": "dylan@example.com",
            "has_worked_with_campbells_before": "no",
            "problem_or_request": "no heat from furnace",
            "problem_details": "no heat from 20 year old gas furnace, blower runs but air is cold",
            "transcript": "assistant: hidden",
        }

        self.assertEqual(
            build_lead_email_subject(summary),
            "Service Request - Dylan Lee - no heat from furnace",
        )
        self.assertEqual(
            build_lead_email_text(summary),
            "\n".join(
                [
                    "Name: Dylan Lee",
                    "Phone: +14065551212",
                    "Address: 123 Main St",
                    "Email: dylan@example.com",
                    "Existing Customer: no",
                    "Problem: no heat from 20 year old gas furnace, blower runs but air is cold",
                ]
            ),
        )

    def test_followup_sms_body_mentions_issue(self):
        body = build_followup_sms_body({"problem_or_request": "I have a leaking faucet."})

        self.assertIn("leaking faucet", body)
        self.assertIn("professional will reach out", body)

    def test_phone_number_normalization(self):
        self.assertEqual(normalize_phone_number("(406) 555-1212"), "+14065551212")
        self.assertEqual(normalize_phone_number("+1 406 555 1212"), "+14065551212")

    def test_company_info_aliases(self):
        self.assertIn("$119", get_company_info("trip charge"))
        self.assertIn("Bozeman", get_company_info("service areas"))

    def test_hangup_delay_allows_goodbye_to_play(self):
        self.assertIn("Goodbye", END_CALL_MESSAGE)
        self.assertGreaterEqual(estimate_hangup_delay_seconds(), 4.0)
        self.assertLessEqual(estimate_hangup_delay_seconds(), 8.0)


if __name__ == "__main__":
    unittest.main()
