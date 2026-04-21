from app.extensions import db
from datetime import datetime, timedelta
import uuid

class WriterProfile(db.Model):
    __tablename__ = "writer_profiles"

    id = db.Column(db.String, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey("users.id"), unique=True)

    bio = db.Column(db.Text)
    profile_image = db.Column(db.String(1024))

    specializations = db.Column(db.JSON, default=list)
    subjects = db.Column(db.JSON, default=list)

    education = db.Column(db.JSON, default=list)
    languages = db.Column(db.JSON, default=list)

    profile_completion = db.Column(db.Float, default=0.0)
    is_complete = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)

    user = db.relationship("User", backref="writer_profile")


    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "profile_image": self.profile_image,
            "bio": self.bio or "",
            "specializations": self.specializations or [],
            "subjects": self.subjects or [],
            "education": self.education or [],
            "languages": self.languages or [],
            "is_complete": self.is_complete,
            "profile_completion": self.profile_completion,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
