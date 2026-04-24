from app.extensions import db
from datetime import datetime
import uuid

class InsightPayTransaction(db.Model):
    __tablename__ = "insightpay_transactions"

    id = db.Column(db.String(50), primary_key=True, default=lambda: f"txn_{uuid.uuid4()}")

    user_id = db.Column(
        db.String(50),
        db.ForeignKey("insightpay_users.id"),
        nullable=False,
        index=True
    )

    amount = db.Column(db.Numeric(10,2), nullable=False)

    type = db.Column(db.String(50), nullable=False)
    # survey_reward / withdrawal / admin_adjustment

    status = db.Column(db.String(20), default="completed")  
    # pending / completed / failed

    reference_id = db.Column(db.String(50))
    # survey_attempt_id

    description = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)