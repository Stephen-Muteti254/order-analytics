from app.extensions import db
from datetime import datetime
import uuid

class AssessmentScore(db.Model):
    __tablename__ = "assessment_scores"

    id = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))

    candidate_name = db.Column(db.String(255), nullable=False)
    candidate_email = db.Column(db.String(255), nullable=False, index=True)
    role = db.Column(db.String(255), nullable=False)

    score = db.Column(db.Integer, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "candidateName": self.candidate_name,
            "candidateEmail": self.candidate_email,
            "role": self.role,
            "score": self.score,
            "createdAt": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }