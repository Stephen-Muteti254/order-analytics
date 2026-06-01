from datetime import datetime
from flask import current_app, render_template
from app.extensions import db
from app.models.prospective import Prospective
from app.utils.mailer import send_email
import time

COMPANY_NAME = "Academic Hub"


def send_prospective_email(prospect):
    """
    Sends outreach email to a single prospective client
    """
    try:
        onboarding_url = f"{current_app.config['FRONTEND_URL']}/"

        html = render_template(
            "emails/prospective2.html",
            title="Welcome to Academic Hub",
            full_name=prospect.name or "there",
            onboarding_url=onboarding_url,
            company_name=COMPANY_NAME,
            year=datetime.utcnow().year,
        )

        send_email(
            to=prospect.email,
            subject="Academic support when you need it",
            html=html
        )

        # Optional: mark as contacted
        prospect.contacted_at = datetime.utcnow()
        db.session.commit()

    except Exception as e:
        current_app.logger.error(
            f"Failed to send prospective email to {prospect.email}: {e}"
        )


def send_bulk_prospective_emails(limit=10):
    """
    Sends emails to multiple prospects (batched to avoid overload)
    """
    try:
        prospects = (
            Prospective.query
            .filter(Prospective.contacted_at.is_(None))
            .limit(limit)
            .all()
        )

        sent_count = 0

        for prospect in prospects:
            try:
                send_prospective_email(prospect)
                time.sleep(45)
                sent_count += 1
            except Exception as e:
                current_app.logger.error(
                    f"Error sending to {prospect.email}: {e}"
                )

        current_app.logger.info(f"Sent {sent_count} prospective emails")
        return sent_count

    except Exception as e:
        current_app.logger.error(f"Bulk email failed: {e}")
        return 0
