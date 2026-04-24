from flask import (
    Blueprint,
    request,
    jsonify,
    send_from_directory,
    current_app,
    abort,
    send_file
)
from flask_jwt_extended import jwt_required, get_jwt_identity
from insightpay.services.survey_service import SurveyService
from insightpay.models.survey import Survey
from insightpay.utils.admin_required import admin_required
from datetime import datetime, timedelta, timezone
from app.extensions import db
from insightpay.models.survey_attempt import SurveyAttempt
from insightpay.models.survey_attachment import SurveyAttachment
from insightpay.models.survey_response import SurveyResponse
from insightpay.models.user import InsightPayUser
import os
from pathlib import Path
from insightpay.models.insightpay_transaction import InsightPayTransaction

bp = Blueprint(
    "insightpay_surveys",
    __name__,
    url_prefix="/api/insightpay/admin/surveys"
)


@bp.route("", methods=["GET"])
@jwt_required()
@admin_required
def list_surveys():
    surveys = SurveyService.list_surveys()
    return jsonify({
        "success": True,
        "data": [s.to_dict() for s in surveys]
    })


@bp.route("", methods=["POST"])
@jwt_required()
@admin_required
def create_survey():
    data = request.form.to_dict()
    files = request.files.getlist("attachments")
    admin_id = get_jwt_identity()

    survey = SurveyService.create_survey(data, files, admin_id)
    return jsonify({
        "success": True,
        "data": survey.to_dict()
    }), 201


@bp.route("/<survey_id>", methods=["PUT"])
@jwt_required()
@admin_required
def update_survey(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    data = request.get_json()

    survey = SurveyService.update_survey(survey, data)
    return jsonify({
        "success": True,
        "data": survey.to_dict()
    })


@bp.route("/<survey_id>", methods=["DELETE"])
@jwt_required()
@admin_required
def delete_survey(survey_id):
    survey = Survey.query.get_or_404(survey_id)

    now = datetime.now(timezone.utc)

    # Count active attempts that have not yet expired
    active_attempts = SurveyAttempt.query.filter(
        SurveyAttempt.survey_id == survey.id,
        SurveyAttempt.expires_at > now,  # attempt not yet expired
        SurveyAttempt.status == "active"  # optional, if you track status
    ).count()

    if active_attempts > 0:
        abort(400, "Cannot delete survey with ongoing attempts")

    # Safe to delete survey
    SurveyService.delete_survey(survey)

    return jsonify({"success": True})


user_surveys_bp = Blueprint(
    "insightpay_user_surveys",
    __name__,
    url_prefix="/api/insightpay/surveys"
)


@user_surveys_bp.route("/<survey_id>/complete", methods=["POST"])
@jwt_required()
def complete_survey(survey_id):

    user_id = get_jwt_identity()
    data = request.json or {}
    answers = data.get("answers", {})

    # Always use timezone-aware UTC
    now = datetime.now(timezone.utc)

    attempt = SurveyAttempt.query.filter_by(
        survey_id=survey_id,
        user_id=user_id,
        status="active"
    ).first_or_404()

    if attempt.status == "completed":
        return {
            "success": True,
            "message": "Already completed"
        }

    # Normalize expires_at in case DB returned naive datetime
    expires_at = attempt.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    # Expiration check
    if now > expires_at:
        attempt.status = "expired"
        db.session.commit()
        return {"message": "Survey expired"}, 400

    # Save survey responses
    responses = []

    for question_id, value in answers.items():
        responses.append(
            SurveyResponse(
                attempt_id=attempt.id,
                question_id=question_id,
                answer=value
            )
        )

    db.session.bulk_save_objects(responses)

    # Mark attempt completed
    attempt.completed_at = now
    attempt.status = "completed"

    reward = float(attempt.reward_snapshot)

    # Record financial transaction
    txn = InsightPayTransaction(
        user_id=user_id,
        amount=reward,
        type="survey_reward",
        status="completed",
        reference_id=attempt.id,
        description="Survey reward"
    )

    db.session.add(txn)

    # Credit user balance
    user = InsightPayUser.query.get_or_404(user_id)
    # user.pending_balance += reward
    user.available_balance += reward

    db.session.commit()

    return {
        "success": True,
        "data": {
            "rewardCredited": float(reward)
        }
    }


@user_surveys_bp.route("/<survey_id>/start", methods=["POST"])
@jwt_required()
def start_survey(survey_id):
    user_id = get_jwt_identity()

    # Check if already attempted
    existing_attempt = SurveyAttempt.query.filter_by(
        user_id=user_id,
        survey_id=survey_id
    ).first()

    if existing_attempt:
        abort(400, "You have already attempted this survey.")

    survey = (
        Survey.query
        .filter_by(id=survey_id, is_active=True)
        .with_for_update()
        .first_or_404()
    )

    if survey.slots_remaining <= 0:
        abort(400, "No slots remaining")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=survey.duration_minutes)

    survey.slots_remaining -= 1

    attempt = SurveyAttempt(
        user_id=user_id,
        survey_id=survey.id,
        expires_at=expires_at,
        reward_snapshot=survey.reward
    )

    db.session.add(attempt)
    db.session.commit()

    return jsonify({
        "success": True,
        "attempt": {
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z")
        }
    })


@user_surveys_bp.route("/public", methods=["GET"])
@jwt_required()
def list_active_surveys():
    user_id = get_jwt_identity()
    surveys = SurveyService.list_active_surveys_for_users(user_id)
    return jsonify({
        "success": True,
        "data": [s.to_dict() for s in surveys]
    })


@user_surveys_bp.route("/attachments/<attachment_id>", methods=["GET"])
@jwt_required()
def get_survey_attachment(attachment_id):

    attachment = SurveyAttachment.query.get_or_404(attachment_id)

    root = current_app.config["INSIGHTPAY_SURVEY_UPLOADS_FOLDER"]

    # normalize stored path
    relative_path = Path(attachment.url).as_posix()

    file_path = os.path.abspath(os.path.join(root, relative_path))

    print("ROOT:", root)
    print("REL:", relative_path)
    print("FULL:", file_path)
    print("EXISTS:", os.path.exists(file_path))

    if not os.path.exists(file_path):
        abort(404, "File not found")

    return send_file(
        file_path,
        mimetype=attachment.type,
        as_attachment=False
    )


@user_surveys_bp.route("/<survey_id>", methods=["GET"])
@jwt_required()
def get_survey(survey_id):
    survey = Survey.query.filter_by(
        id=survey_id,
        is_active=True
    ).first_or_404()

    return jsonify({
        "success": True,
        "data": survey.to_dict()
    })