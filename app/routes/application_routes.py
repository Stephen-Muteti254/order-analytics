from flask import (
    Blueprint, request, url_for, send_file, current_app
)
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import db
from app.models.user import User
from app.models.writer_application import WriterApplication
from app.services.application_service import create_writer_application
from app.utils.response_formatter import success_response, error_response
from datetime import datetime
import os 
from flask import current_app
from app.services.email_service import (
    send_application_received_email,
    send_application_approved_email,
    send_application_rejected_email,
    send_deposit_approved_email
)
from flask import current_app, render_template

bp = Blueprint("applications", __name__, url_prefix="/api/v1/applications")

@bp.route("/apply-writer", methods=["POST"])
@jwt_required()
def apply_writer():
    uid = get_jwt_identity()
    user = User.query.get(uid)
    if not user:
        return error_response("NOT_FOUND", "User not found", status=404)

    try:
        form_data = request.form.to_dict()
        files = request.files

        app = create_writer_application(user, form_data, files)
        send_application_received_email(user)
        return success_response({
            "success": True,
            "message": "Application submitted successfully",
            "application_id": app.id,
            "status": "pending"
        })
    except Exception as e:
        db.session.rollback()
        print(f"error = {str(e)}")
        return error_response("APPLICATION_ERROR", str(e), status=400)


def admin_required(user):
    """Ensure that the current user has admin privileges."""
    if not user or not (user.role.lower() in ("admin", "super_admin")):
        return False
    return True


# ------------------------------------------
# 1. LIST ALL APPLICATIONS
# ------------------------------------------
@bp.route("/all", methods=["GET"])
@jwt_required()
def list_applications():
    uid = get_jwt_identity()
    user = User.query.get(uid)

    if not admin_required(user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    status = request.args.get("status")
    search = request.args.get("search", "").strip()

    query = (
        db.session.query(WriterApplication, User)
        .join(User, WriterApplication.user_id == User.id)
        .order_by(WriterApplication.created_at.desc())
    )

    if status and status != "all":
        query = query.filter(WriterApplication.status == status)

    if search:
        query = query.filter(
            db.or_(
                WriterApplication.id.ilike(f"%{search}%"),
                User.full_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
            )
        )

    apps = query.all()

    data = [
        {
            "id": a.id,
            "user_id": u.id,
            "user_name": u.full_name,
            "email": u.email,
            "status": a.status,
            "account_status": u.account_status,
            "application_status": u.application_status,
            "submitted_at": a.created_at.isoformat(),
        }
        for a, u in apps
    ]

    return success_response(data)

# ------------------------------------------
# 2. GET APPLICATION DETAILS
# ------------------------------------------

@bp.route("/<string:application_id>", methods=["GET"])
@jwt_required()
def get_application_details(application_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    if not admin_required(user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    app = WriterApplication.query.get(application_id)
    if not app:
        return error_response("NOT_FOUND", "Application not found", status=404)

    user_data = app.user
    response_data = {
        "id": app.id,
        "user_id": user_data.id,
        "user_name": user_data.full_name,
        "user_email": user_data.email,
        "country": app.country,
        "city": app.city,
        "education": app.education,
        "specialization": app.specialization,
        "years_experience": app.years_experience,
        "proficiency_answers": app.proficiency_answers,
        "selected_prompt": app.selected_prompt,
        "prompt_response": app.prompt_response,
        "selected_essay_topic": app.selected_essay_topic,
        "essay_file_url": (
            url_for("applications.serve_file",
                filename=os.path.relpath(app.essay_file_path, current_app.config.get("UPLOAD_FOLDER")),
                _external=True
            ) if app.essay_file_path else None
        ),
        "cv_file_url": (
            url_for("applications.serve_file",
                filename=os.path.relpath(app.cv_file_path, current_app.config.get("UPLOAD_FOLDER")),
                _external=True
            ) if app.cv_file_path else None
        ),
        "degree_certificates": [
            url_for("applications.serve_file",
                filename=os.path.relpath(f, current_app.config.get("UPLOAD_FOLDER")),
                _external=True
            )
            for f in (app.degree_certificates or [])
        ],
        "work_samples": [
            url_for("applications.serve_file",
                filename=os.path.relpath(f, current_app.config.get("UPLOAD_FOLDER")),
                _external=True
            )
            for f in (app.work_samples or [])
        ],
        "status": app.status,
        "admin_feedback": getattr(app, "admin_feedback", None),
        "submitted_at": app.created_at.isoformat(),
    }

    return success_response(response_data)


# ------------------------------------------
# 3. APPROVE APPLICATION
# ------------------------------------------
@bp.route("/<string:application_id>/approve", methods=["POST"])
@jwt_required()
def approve_application(application_id):
    uid = get_jwt_identity()
    admin_user = User.query.get(uid)
    if not admin_required(admin_user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    app = WriterApplication.query.get(application_id)
    if not app:
        return error_response("NOT_FOUND", "Application not found", status=404)

    if app.status in ["awaiting_initial_deposit", "paid_initial_deposit", "approved", "rejected"]:
        return error_response("INVALID_STATUS", "Application has already been processed", status=400)

    data = request.get_json() or {}
    feedback = data.get("feedback", "")

    try:
        app.status = "approved"
        app.admin_feedback = feedback
        app.updated_at = datetime.utcnow()

        # Update user role and status
        user = app.user
        user.application_status = "awaiting_initial_deposit"
        user.account_status = "awaiting_initial_deposit"

        db.session.commit()

        send_application_approved_email(user, feedback)

        return success_response({
            "message": "Application approved successfully",
            "application_id": app.id,
            "status": app.status,
            "user_id": user.id,
            "approved_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        db.session.rollback()
        return error_response("SERVER_ERROR", str(e), status=500)


# ------------------------------------------
# 4. REJECT APPLICATION
# ------------------------------------------
@bp.route("/<string:application_id>/reject", methods=["POST"])
@jwt_required()
def reject_application(application_id):
    uid = get_jwt_identity()
    admin_user = User.query.get(uid)
    if not admin_required(admin_user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    app = WriterApplication.query.get(application_id)
    if not app:
        return error_response("NOT_FOUND", "Application not found", status=404)

    if app.status in ["awaiting_initial_deposit", "approved", "rejected"]:
        return error_response("INVALID_STATUS", "Application has already been processed", status=400)

    data = request.get_json() or {}
    feedback = data.get("feedback")
    if not feedback:
        return error_response("VALIDATION_ERROR", "Feedback is required when rejecting an application", status=400)

    try:
        app.status = "rejected"
        app.admin_feedback = feedback
        app.updated_at = datetime.utcnow()

        user = app.user
        user.application_status = "rejected"
        user.account_status = "rejected"

        db.session.commit()

        send_application_rejected_email(user, feedback)

        return success_response({
            "message": "Application rejected",
            "application_id": app.id,
            "status": app.status,
            "rejected_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        db.session.rollback()
        return error_response("SERVER_ERROR", str(e), status=500)


@bp.route("/files/<path:filename>", methods=["GET"])
@jwt_required(optional=True)
def serve_file(filename):
    """
    Securely serve uploaded files for preview/download.
    Supports both Authorization header and ?token= query param.
    """
    from flask_jwt_extended import decode_token

    uid = get_jwt_identity()

    # Support access via ?token= if no JWT header is provided
    if not uid and "token" in request.args:
        token = request.args.get("token")
        try:
            decoded = decode_token(token)
            uid = decoded.get("sub")
        except Exception as e:
            print(f"Token decode failed: {e}")
            return error_response("UNAUTHORIZED", "Invalid or expired token", status=401)

    user = User.query.get(uid)

    if not user:
        return error_response("FORBIDDEN", "User not found or unauthorized", status=403)

    if not admin_required(user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    try:
        upload_folder = current_app.config.get("UPLOAD_FOLDER")

        safe_path = os.path.abspath(os.path.join(upload_folder, filename))

        if not safe_path.startswith(os.path.abspath(upload_folder)):
            return error_response("FORBIDDEN", "Invalid file path", status=403)

        if not os.path.exists(safe_path):
            return error_response("NOT_FOUND", "File not found", status=404)

        # Guess mimetype
        ext = filename.lower()
        if ext.endswith(".pdf"):
            mimetype = "application/pdf"
        elif ext.endswith((".jpg", ".jpeg")):
            mimetype = "image/jpeg"
        elif ext.endswith(".png"):
            mimetype = "image/png"
        elif ext.endswith(".docx"):
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            mimetype = "application/octet-stream"

        return send_file(safe_path, mimetype=mimetype, as_attachment=False)

    except Exception as e:
        return error_response("FILE_ERROR", str(e), status=500)


# ------------------------------------------
# 5. CONFIRM INITIAL DEPOSIT (ADMIN)
# ------------------------------------------
@bp.route("/<string:user_id>/confirm-deposit", methods=["POST"])
@jwt_required()
def confirm_initial_deposit(user_id):
    uid = get_jwt_identity()
    admin_user = User.query.get(uid)

    if not admin_required(admin_user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    # Find application by user_id (NOT application.id)
    application = WriterApplication.query.filter_by(user_id=user_id).first()
    if not application:
        return error_response("NOT_FOUND", "Application not found for this user", status=404)

    if application.status != "approved":
        return error_response(
            "INVALID_STATUS",
            "Application must be approved before confirming deposit",
            status=400
        )

    user = application.user

    try:
        # Activate writer
        user.application_status = "paid_initial_deposit"
        user.account_status = "paid_initial_deposit"
        user.role = "writer"

        db.session.commit()
        send_deposit_approved_email(user)

        return success_response({
            "message": "Initial deposit confirmed. Writer activated.",
            "user_id": user.id,
            "application_id": application.id,
            "application_status": application.status,
            "account_status": user.account_status,
            "activated_at": datetime.utcnow().isoformat()
        })

    except Exception as e:
        db.session.rollback()
        return error_response("SERVER_ERROR", str(e), status=500)


@bp.route("/_test/email/application-approved")
def test_application_approved_email():
    class DummyUser:
        full_name = "Stephen Muteti"
        email = "test@example.com"

    return render_template(
        "emails/application_approved.html",
        title="Application Approved",
        full_name="Test Writer",
        feedback="Great academic background and strong samples.",
        onboarding_url="https://academichubpro.com/writer",
        company_name="Academic Hub",
        year=datetime.utcnow().year,
    )


@bp.route("/_test/email/application-rejected")
def test_application_rejected_email():
    return render_template(
        "emails/application_rejected.html",
        title="Application Update",
        full_name="Stephen Muteti",
        feedback="Your samples need stronger academic formatting.",
        company_name="Academic Hub",
        year=datetime.utcnow().year,
    )
