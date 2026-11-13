import os

import requests


def send(provider: str, subject: str, html_body: str, text_body: str):
    to = os.environ["F1_EMAIL_TO"]
    sender = os.environ.get("F1_EMAIL_FROM", "F1 Digest <onboarding@resend.dev>")
    if provider == "resend":
        r = requests.post("https://api.resend.com/emails", timeout=30,
                          headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                          json={"from": sender, "to": [to], "subject": subject, "html": html_body, "text": text_body})
    elif provider == "brevo":
        name, _, addr = sender.partition("<")
        r = requests.post("https://api.brevo.com/v3/smtp/email", timeout=30,
                          headers={"api-key": os.environ["BREVO_API_KEY"]},
                          json={"sender": {"name": name.strip() or "F1 Digest", "email": (addr or name).strip(" >")},
                                "to": [{"email": to}], "subject": subject,
                                "htmlContent": html_body, "textContent": text_body})
    else:
        raise ValueError(f"unknown email provider: {provider}")
    r.raise_for_status()
