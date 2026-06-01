from app.extensions import db
from datetime import datetime
import uuid

def gen_uuid(prefix=None):
    uid = str(uuid.uuid4())
    return f"{prefix}-{uid}" if prefix else uid

class Prospective(db.Model):
    __tablename__ = "prospectives"

    id = db.Column(db.String(50), primary_key=True, default=lambda: gen_uuid("prp"))
    name = db.Column(db.String(255))
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)

    contacted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)