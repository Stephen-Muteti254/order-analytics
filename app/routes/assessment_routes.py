from flask import Blueprint, request
from app.extensions import db
from app.models.assessment_score import AssessmentScore
from app.utils.response_formatter import success_response, error_response

bp = Blueprint("assessments", __name__, url_prefix="/api/v1")

@bp.route("/assessments/scores", methods=["POST"])
def submit_score():
    data = request.get_json()

    if not data:
        return error_response("VALIDATION_ERROR", "Invalid payload", status=400)

    candidate_name = data.get("candidateName")
    candidate_email = data.get("candidateEmail").strip().lower()
    role = data.get("role")
    score = data.get("score")

    # ---- validation ----
    if not all([candidate_name, candidate_email, role, score is not None]):
        return error_response(
            "VALIDATION_ERROR",
            "candidateName, candidateEmail, role, and score are required",
            status=400
        )

    if not isinstance(score, int) or score < 0 or score > 100:
        return error_response(
            "VALIDATION_ERROR",
            "Score must be an integer between 0 and 100",
            status=400
        )

    existing = AssessmentScore.query.filter_by(
	    candidate_email=candidate_email,
	    role=role
	).first()

    if existing:
	    return error_response(
	        "DUPLICATE_SUBMISSION",
	        "You have already submitted this assessment",
	        status=409
	    )

    try:
        record = AssessmentScore(
            candidate_name=candidate_name,
            candidate_email=candidate_email,
            role=role,
            score=score
        )

        db.session.add(record)
        db.session.commit()

        return success_response({
            "id": record.id,
            "createdAt": record.created_at.isoformat() + "Z"
        }, status=201)

    except Exception as e:
        db.session.rollback()
        return error_response("CREATE_ERROR", str(e), status=500)