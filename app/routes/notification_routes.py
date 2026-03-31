from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.services.notification_service import (
    get_user_notifications,
    mark_notification_read,
    mark_all_read_for_user,
)
from app.utils.response_formatter import success_response, error_response
from app.utils.pagination import paginate_query
from app.models.notification import Notification

from app.models.user import User
from app.models.notification_read import NotificationRead
from app.models.notification import Notification
from app.extensions import db
from datetime import datetime

from app.services.notification_service import (
    send_notification_to_user,
    send_notification_to_group,
    send_notification_to_all
)
from app.services.email_service import send_notification_email

bp = Blueprint("notifications", __name__, url_prefix="/api/v1/notifications")

def admin_required(user):
    return user and (user.role.lower() == "admin" or user.role.lower() == "super_admin")

@bp.route("/send", methods=["POST"])
@jwt_required()
def send_notification():
    uid = get_jwt_identity()
    admin_user = User.query.get(uid)

    if not admin_required(admin_user):
        return error_response("FORBIDDEN", "Admin privileges required", status=403)

    data = request.get_json() or {}
    title = data.get("title")
    message = data.get("message")
    notif_type = data.get("type", "info")
    recipients = data.get("recipients", "all")
    user_email = data.get("user_email")
    sender_team = data.get("sender_team", "Support Team")

    if not title or not message:
        return error_response("VALIDATION_ERROR", "Title and message are required", 400)

    if recipients == "user":
        if not user_email:
            return error_response("VALIDATION_ERROR", "user_email is required", 400)

        user = User.query.filter_by(email=user_email).first()
        if not user:
            return error_response("NOT_FOUND", "User not found", 404)

        notif = send_notification_to_user(
            user_email, title, message, notif_type, sender_id=uid, sender_team=sender_team
        )

        send_notification_email(user, title, message, sender_team=sender_team)

        return success_response({
            "message": "Notification sent successfully (individual)",
            "notification_id": notif.id,
        })

    elif recipients in ["writers", "clients"]:
        count = send_notification_to_group(
            recipients, title, message, notif_type, sender_id=uid, sender_team=sender_team
        )
        return success_response({
            "message": f"Notification sent successfully to {count} users (group: {recipients})",
        })

    else:
        count = send_notification_to_all(
            title, message, notif_type, sender_id=uid, sender_team=sender_team
        )
        return success_response({
            "message": f"Notification sent successfully to {count} users (all)",
        })


@bp.route("", methods=["GET"])
@jwt_required()
def get_notifications():
    uid = get_jwt_identity()
    user = User.query.get(uid)
    if not user:
        return error_response("UNAUTHORIZED", "Invalid user", 401)

    limit = int(request.args.get("limit", 20))

    # Fetch or create NotificationRead record
    notif_read = NotificationRead.query.filter_by(user_id=uid).first()
    if notif_read is None:
        notif_read = NotificationRead(
            user_id=uid,
            last_read=datetime(1970, 1, 1)
        )
        db.session.add(notif_read)
        db.session.commit()

    # ---- IMPORTANT FIX HERE ----
    # Do NOT show notifications older than when the user joined
    q = Notification.query.filter(
        Notification.created_at >= user.joined_at,   # <- FIX
        (
            (Notification.target_type == "all") |
            ((Notification.target_type == "group") & (Notification.target_group == user.role)) |
            ((Notification.target_type == "individual") & (Notification.user_email == user.email))
        )
    )

    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    offset = (page - 1) * limit

    total_items = q.count()
    total_pages = (total_items + limit - 1) // limit

    notifications = (
        q.order_by(Notification.created_at.desc())
         .offset(offset)
         .limit(limit)
         .all()
    )

    # Determine read/unread
    results = [{
        "id": n.id,
        "title": n.title,
        "message": n.message,
        "type": n.type,
        "target_type": n.target_type,
        "target_group": n.target_group,
        "created_at": n.created_at.isoformat(),
        "is_read": n.created_at <= notif_read.last_read
    } for n in notifications]

    return success_response({
        "notifications": results,
        "pagination": {
            "page": page,
            "limit": limit,
            "total_items": total_items,
            "total_pages": total_pages,
        }
    })


@bp.route("/mark-seen", methods=["POST"])
@jwt_required()
def mark_seen():
    uid = get_jwt_identity()
    notif_read = NotificationRead.query.filter_by(user_id=uid).first()
    now = datetime.utcnow()

    if notif_read is None:
        notif_read = NotificationRead(user_id=uid, last_read=now)
        db.session.add(notif_read)
    else:
        notif_read.last_read = now

    db.session.commit()
    return success_response({"message": "Notifications marked as seen", "last_read": now.isoformat()})
