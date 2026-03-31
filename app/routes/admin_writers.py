from datetime import datetime, timedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import db
from app.models.user import User
from app.utils.response_formatter import success_response, error_response
from sqlalchemy import or_, and_

from app.services.email_service import (
    send_deposit_approved_email,
    send_account_suspension_email
)

from app.models.account_suspensions import AccountSuspension
from app.services.email_service import (
    send_account_suspension_email,
    send_account_reactivated_email
)

bp = Blueprint("admin_writers", __name__, url_prefix="/api/v1/admin/writers")

def admin_required(user):
    return user and (user.role.lower() in ("admin", "super_admin"))

@bp.route("", methods=["GET"])
@jwt_required()
def list_writers():
    uid = get_jwt_identity()
    admin = User.query.get(uid)
    if not admin_required(admin):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    writers = User.query.filter(
        User.role == "writer",
        User.application_status == "paid_initial_deposit",
        User.account_status.in_([
            "paid_initial_deposit",
            "suspended_temporary",
            "suspended_permanent"
        ])
    ).all()


    data = [
        {
            "id": w.id,
            "email": w.email,
            "full_name": w.full_name,
            "rating": w.rating,
            "completed_orders": w.completed_orders,
            "total_earned": w.total_earned,
            "joined_at": w.joined_at.isoformat() if w.joined_at else None,
            "status": "active" if w.is_verified else "suspended-temporary",
            "account_status": w.account_status
        }
        for w in writers
    ]

    return success_response({"writers": data})


@bp.route("/<string:user_id>/suspend", methods=["PATCH"])
@jwt_required()
def suspend_writer(user_id):
    admin = User.query.get(get_jwt_identity())
    if not admin_required(admin):
        return error_response("FORBIDDEN", "Admin privileges required", 403)

    writer = User.query.get(user_id)
    if not writer:
        return error_response("NOT_FOUND", "Writer not found", 404)

    data = request.get_json()
    suspension_type = data["type"]
    reasons = data["reasons"]
    notes = data.get("notes")
    days = data.get("days") or data.get("duration_days")

    if suspension_type == "temporary":
        if not isinstance(days, int) or days <= 0:
            return error_response(
                "INVALID_REQUEST",
                "Temporary suspension requires a valid 'days' value",
                400
            )
        suspended_until = datetime.utcnow() + timedelta(days=days)
    else:
        suspended_until = None


    suspension = AccountSuspension(
        user_id=writer.id,
        suspension_type=suspension_type,
        reasons=reasons,
        notes=notes,
        admin_id=admin.id,
        suspended_until=suspended_until
    )

    writer.account_status = (
        "suspended_temporary"
        if suspension_type == "temporary"
        else "suspended_permanent"
    )

    db.session.add(suspension)
    db.session.commit()

    # EMAIL NOTIFICATION (non-blocking)
    try:
        send_account_suspension_email(
            user=writer,
            suspension_type=suspension_type,
            reasons=reasons,
            notes=notes,
            suspended_until=suspended_until
        )
    except Exception as e:
        print(
            f"Suspension email failed for user_id={writer.id}: {e}"
        )

    return success_response({"message": "Writer suspended"})


from flask import current_app

@bp.route("/<string:user_id>/activate", methods=["PATCH"])
@jwt_required()
def activate_writer(user_id):
    admin = User.query.get(get_jwt_identity())
    if not admin_required(admin):
        return error_response("FORBIDDEN", "Admin privileges required", 403)

    writer = User.query.get(user_id)
    if not writer:
        return error_response("NOT_FOUND", "Writer not found", 404)

    # If already active, exit gracefully
    if writer.account_status == "paid_initial_deposit":
        return success_response({
            "message": "Writer account is already active"
        })

    # Deactivate all active suspensions
    active_suspensions = AccountSuspension.query.filter(
        AccountSuspension.user_id == writer.id,
        AccountSuspension.is_active == True
    ).all()

    for suspension in active_suspensions:
        suspension.is_active = False
        suspension.suspended_until = datetime.utcnow()

    # Restore account status
    writer.account_status = "paid_initial_deposit"

    db.session.commit()

    # EMAIL NOTIFICATION (non-blocking)
    try:
        send_account_reactivated_email(writer)
    except Exception as e:
        print(
            f"Reactivation email failed for user_id={writer.id}: {e}"
        )

    return success_response({
        "message": "Writer account reactivated successfully"
    })
