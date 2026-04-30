import logging
import sys
import traceback
import resend
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _log(msg: str) -> None:
    # print to stdout so Render captures it regardless of root-logger config
    print(f"[email_service] {msg}", file=sys.stdout, flush=True)


def _client_configured() -> bool:
    key = settings.resend_api_key
    if not key:
        _log("ABORT: settings.resend_api_key is empty or missing")
        return False
    _log(f"resend_api_key present (length={len(key)})")
    _log(f"email_from={settings.email_from!r}")
    _log(f"app_base_url={settings.app_base_url!r}")
    resend.api_key = key
    return True


def send_password_reset_email(to: str, reset_url: str) -> None:
    _log(f"send_password_reset_email called: to={to!r}")
    if not _client_configured():
        _log("send aborted: client not configured")
        return
    _log(f"about to call resend.Emails.send: from={settings.email_from!r}, to={to!r}, subject='Set your Primed password'")
    _log(f"reset_url={reset_url}")
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 480px; margin: 0 auto; padding: 24px; color: #1a1a1a;">
      <h2 style="font-size: 20px; font-weight: 600; margin: 0 0 16px;">Set your Primed password</h2>
      <p style="font-size: 15px; line-height: 1.5; margin: 0 0 24px;">
        Click the button below to set a new password for your account. This link expires in {settings.password_reset_token_expire_minutes} minutes.
      </p>
      <p style="margin: 0 0 24px;">
        <a href="{reset_url}" style="display: inline-block; padding: 12px 24px; background: #e05a33; color: #fff; text-decoration: none; border-radius: 8px; font-weight: 600;">Set password</a>
      </p>
      <p style="font-size: 13px; color: rgba(0,0,0,0.5); line-height: 1.5; margin: 0;">
        If you didn't request this, you can safely ignore this email. The link won't do anything unless you click it.
      </p>
    </div>
    """
    text = (
        f"Set your Primed password by visiting: {reset_url}\n\n"
        f"This link expires in {settings.password_reset_token_expire_minutes} minutes. "
        "If you didn't request this, you can safely ignore this email."
    )
    try:
        response = resend.Emails.send({
            "from": settings.email_from,
            "to": [to],
            "subject": "Set your Primed password",
            "html": html,
            "text": text,
        })
        _log(f"resend.Emails.send returned: {response!r}")
    except Exception as e:
        _log(f"resend.Emails.send raised: {type(e).__name__}: {e}")
        _log(f"traceback:\n{traceback.format_exc()}")
