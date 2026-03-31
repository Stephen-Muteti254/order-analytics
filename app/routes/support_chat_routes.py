import uuid
import os
from flask import Blueprint, request, current_app, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta

from app.extensions import db
from app.models.support_chat import SupportChat
from app.models.support_message import SupportMessage
from app.models.user import User
from app.services.support_chat_service import (
    get_or_create_support_chat,
    add_support_message,
    save_support_file
)
from app.services.chat_behavior_analyzer import analyze_chat_behavior
from app.utils.response_formatter import success_response, error_response
from app.models.user import User
from mimetypes import guess_type
from sqlalchemy import desc
from sqlalchemy import func

bp = Blueprint("support_chat", __name__, url_prefix="/api/v1/support-chat")

@bp.route("", methods=["POST"])
@jwt_required()
def create_or_get():
    uid = get_jwt_identity()
    chat = get_or_create_support_chat(uid)

    print(f"[SupportChat] User {uid} chat_id={chat.id}")

    return success_response({
        "chat": {
            "id": chat.id,
            "user_id": chat.user_id,
            "created_at": chat.created_at.isoformat() + "Z"
        }
    })

@bp.route("/<chat_id>/messages", methods=["GET"])
@jwt_required()
def list_messages(chat_id):
    uid = get_jwt_identity()
    chat = SupportChat.query.get(chat_id)

    print(f"[SupportChat] list_messages called with chat_id={chat_id} for user {uid}")

    if not chat:
        print(f"[SupportChat] Chat not found: {chat_id}")
        return error_response("NOT_FOUND", "Chat not found", 404)

    messages_q = (
        SupportMessage.query
        .filter_by(support_chat_id=chat_id)
        .order_by(SupportMessage.created_at.asc())
        .all()
    )

    messages = [{
        "id": m.id,
        "sender": {
            "id": m.sender.id,
            "name": m.sender.full_name,
            "avatar": m.sender.profile_image,
            "role": m.sender.role
        },
        "content": m.content,
        "sent_at": m.created_at.isoformat() + "Z",
        "is_read": m.is_read,
        "attachments": m.attachments or []
    } for m in messages_q]

    return success_response({
        "messages": messages,
        "warning": (
            {
                "active": chat.warning_active,
                "risk": chat.warning_risk,
                "message": chat.warning_message,
                "expires_at": chat.warning_expires_at.isoformat() + "Z"
            }
            if chat.warning_active and chat.warning_for_user_id == uid
            else None
        )
    })


@bp.route("/<chat_id>/messages", methods=["POST"])
@jwt_required()
def post_message(chat_id):
    uid = get_jwt_identity()
    chat = SupportChat.query.get(chat_id)

    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    files = []
    content = ""

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        content = request.form.get("content", "")
        files = request.files.getlist("files")
    else:
        data = request.get_json(silent=True) or {}
        content = data.get("content", "")

    if not content and not files:
        return error_response("VALIDATION_ERROR", "Message or file required", 422)

    msg = add_support_message(chat_id, uid, content)

    # ---------- Save files ----------
    saved_files = []
    for f in files:
        fname, _ = save_support_file(f, chat_id, msg.id)
        saved_files.append({
            "id": uuid.uuid4().hex,
            "filename": f.filename,
            "mime": f.mimetype,
            "url": f"/support-chat/files/{chat_id}/{msg.id}/{fname}"
        })

    msg.attachments = saved_files
    db.session.commit()

    # ---------- Behavior analysis ----------
    history = (
        SupportMessage.query
        .filter_by(support_chat_id=chat_id)
        .order_by(SupportMessage.created_at.desc())
        .limit(25)
        .all()[::-1]
    )

    analysis = analyze_chat_behavior(history)
    warning = None

    if analysis["risk"] in ("medium", "high"):
        chat.warning_active = True
        chat.warning_risk = analysis["risk"]
        chat.warning_message = (
            "We detected attempts to share personal or contact information. "
            "Please keep communication within the platform."
        )
        chat.warning_expires_at = datetime.utcnow() + timedelta(days=7)
        chat.warning_for_user_id = uid
        db.session.commit()

        warning = {
            "risk": chat.warning_risk,
            "message": chat.warning_message,
            "expires_at": chat.warning_expires_at.isoformat() + "Z",
        }

    return success_response({
        "id": msg.id,
        "chat_id": msg.support_chat_id,
        "content": msg.content,
        "sent_at": msg.created_at.isoformat() + "Z",
        "is_read": msg.is_read,
        "attachments": msg.attachments,
        "sender": {
            "id": msg.sender.id,
            "name": msg.sender.full_name,
            "avatar": msg.sender.profile_image,
            "role": msg.sender.role
        },
        "warning": warning
    })


@bp.route("/files/<chat_id>/<message_id>/<filename>", methods=["GET"])
@jwt_required()
def get_support_file(chat_id, message_id, filename):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    chat = SupportChat.query.get_or_404(chat_id)

    if chat.user_id != uid and not user.role.lower() in ("admin", "super_admin"):
        return error_response("FORBIDDEN", "Access denied", 403)

    root_dir = current_app.config["SUPPORT_UPLOADS_FOLDER"]
    file_path = os.path.join(root_dir, chat_id, message_id, filename)

    if not os.path.exists(file_path):
        return error_response("NOT_FOUND", "File not found", 404)

    mime, _ = guess_type(file_path)

    return send_file(
        file_path,
        mimetype=mime,
        as_attachment=False
    )


@bp.route("", methods=["GET"])
@jwt_required()
def list_support_chats():
    admin_id = get_jwt_identity()
    admin = User.query.get(admin_id)

    if not admin.role.lower() in ("admin", "super_admin"):
        return error_response("FORBIDDEN", "Admins only", 403)

    # ---- Pagination params ----
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(int(request.args.get("limit", 20)), 100)

    base_query = SupportChat.query.order_by(
        desc(SupportChat.created_at)
    )

    pagination = base_query.paginate(
        page=page,
        per_page=limit,
        error_out=False
    )

    chats = pagination.items

    results = []

    unread_counts = (
        db.session.query(
            SupportMessage.support_chat_id,
            func.count(SupportMessage.id).label("unread_count")
        )
        .filter_by(is_read=False)
        .group_by(SupportMessage.support_chat_id)
        .all()
    )
    unread_map = {chat_id: count for chat_id, count in unread_counts}

    for chat in chats:
        unread_count = unread_map.get(chat.id, 0)

        user = User.query.get(chat.user_id)

        last_msg = (
            SupportMessage.query
            .filter_by(support_chat_id=chat.id)
            .order_by(SupportMessage.created_at.desc())
            .first()
        )

        results.append({
            "id": chat.id,
            "user": {
                "id": user.id,
                "name": user.full_name or "User",
                "role": user.role,
            },
            "last_message": last_msg.content if last_msg else "",
            "last_message_at": (
                last_msg.created_at.isoformat() + "Z"
                if last_msg else None
            ),
            "unread_count": unread_count
        })

    return success_response({
        "chats": results,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": pagination.total,
            "pages": pagination.pages,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev
        }
    })


@bp.route("/<chat_id>/mark-read", methods=["POST"])
@jwt_required()
def mark_chat_read(chat_id):
    uid = get_jwt_identity()
    chat = SupportChat.query.get_or_404(chat_id)

    SupportMessage.query.filter_by(
        support_chat_id=chat_id,
        is_read=False
    ).update({ "is_read": True })

    chat.unread_count = 0
    db.session.commit()

    return success_response({ "status": "ok" })


@bp.route("/<chat_id>/resolve", methods=["POST"])
@jwt_required()
def resolve_chat(chat_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)

    if not user.role.lower() in ("admin", "super_admin"):
        return error_response("FORBIDDEN", "Admins only", 403)

    chat = SupportChat.query.get_or_404(chat_id)
    chat.status = "resolved"
    db.session.commit()

    return success_response({ "status": "resolved" })
