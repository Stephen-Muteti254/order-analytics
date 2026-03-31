from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta

from app.services.chat_service import (
    get_or_create_chat,
    add_message,
    sanitize_message,
)
from app.services.chat_behavior_analyzer import analyze_chat_behavior
from app.utils.response_formatter import success_response, error_response
from app.models.chat import Chat
from app.models.message import Message
from app.extensions import db
from app.models.user import User
from sqlalchemy import func

bp = Blueprint("chat", __name__, url_prefix="/api/v1/chats")


# -----------------------------------------------------------
# CREATE OR GET CHAT
# -----------------------------------------------------------
@bp.route("", methods=["POST"])
@jwt_required()
def create_or_get_chat():
    data = request.get_json() or {}

    order_id = data.get("order_id")
    writer_id = data.get("writer_id")
    client_id = data.get("client_id")

    print(f"order = {order_id} client = {client_id} writer = {writer_id}")
    uid = get_jwt_identity()

    if not order_id:
        return error_response("VALIDATION_ERROR", "order_id is required", 422)

    # ----------------------------------
    # Determine role of caller
    # ----------------------------------
    user = User.query.get(uid)

    if user.role.lower() == "client":
        # Client is starting the chat
        # They MUST be the client
        client_id = uid  

        if not writer_id:
            return error_response(
                "VALIDATION_ERROR",
                "writer_id is required when client starts chat",
                422
            )

    elif user.role.lower() == "writer":
        # Writer is starting the chat
        writer_id = uid   # enforce writer = caller

        if not client_id:
            return error_response(
                "VALIDATION_ERROR",
                "client_id is required when writer starts chat",
                422
            )
    else:
        return error_response(
            "UNAUTHORIZED",
            "We could not sufficiently identify you",
            403
        )

    # Final validation
    if not writer_id or not client_id:
        return error_response(
            "VALIDATION_ERROR",
            "writer_id and client_id are required",
            422
        )

    # Create or fetch existing chat
    chat = get_or_create_chat(order_id, client_id, writer_id)

    last_msg = (
        Message.query.filter_by(chat_id=chat.id)
        .order_by(Message.created_at.desc())
        .first()
    )

    return success_response({
        "chat": {
            "id": chat.id,
            "order_id": chat.order_id,
            "client_id": chat.client_id,
            "writer_id": chat.writer_id,
            "created_at": chat.created_at.isoformat() + "Z",
            "last_message": {
                "content": last_msg.content,
                "sent_at": last_msg.created_at.isoformat() + "Z",
                "is_read": last_msg.is_read,
            } if last_msg else None,
            "unread_count": Message.query.filter(
                Message.chat_id == chat.id,
                Message.sender_id != uid,
                Message.is_read == False
            ).count()
        }
    })


# -----------------------------------------------------------
# LIST CHATS
# -----------------------------------------------------------
@bp.route("", methods=["GET"])
@jwt_required()
def list_chats():
    uid = get_jwt_identity()
    
    # Pagination
    try:
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 10))
    except ValueError:
        page = 1
        limit = 10

    offset = (page - 1) * limit

    chats_q = (
        db.session.query(Chat)
        .outerjoin(Message, Chat.id == Message.chat_id)
        .filter(
            (Chat.client_id == uid) | (Chat.writer_id == uid)
        )
        .group_by(Chat.id)
        .order_by(func.max(Message.created_at).desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    out = []
    for chat in chats_q:
        if chat.warning_active and chat.warning_expires_at and chat.warning_expires_at < datetime.utcnow():
            chat.warning_active = False
            chat.warning_risk = None
            chat.warning_message = None
            chat.warning_expires_at = None
            db.session.commit()

        last_msg = (
            Message.query.filter_by(chat_id=chat.id)
            .order_by(Message.created_at.desc())
            .first()
        )

        other_user = chat.writer if chat.client_id == uid else chat.client

        out.append({
            "id": chat.id,
            "order_id": chat.order_id,
            "order_title": chat.order.title if chat.order else None,
            "other_user": {
                "id": other_user.id if other_user else None,
                "name": other_user.full_name if other_user else None,
                "avatar": other_user.profile_image if other_user else None,
                "role": other_user.role if other_user else None,
            },
            "warning": (
                {
                    "active": chat.warning_active,
                    "risk": chat.warning_risk,
                    "message": chat.warning_message,
                    "expires_at": chat.warning_expires_at.isoformat() + "Z"
                }
                if chat.warning_active and chat.warning_for_user_id == uid
                else None
            ),
            "last_message": {
                "content": last_msg.content,
                "sent_at": last_msg.created_at.isoformat() + "Z",
                "is_read": last_msg.is_read,
            } if last_msg else None,
            "unread_count": Message.query.filter(
                Message.chat_id == chat.id,
                Message.sender_id != uid,
                Message.is_read == False
            ).count()
        })

    return success_response({
        "chats": out,
        "page": page,
        "limit": limit,
        "has_more": len(chats_q) == limit
    })


# -----------------------------------------------------------
# LIST MESSAGES
# -----------------------------------------------------------
@bp.route("/<chat_id>/messages", methods=["GET"])
@jwt_required()
def list_messages(chat_id):
    uid = get_jwt_identity()
    
    chat = Chat.query.get(chat_id)
    
    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))

    msgs_q = Message.query.filter_by(chat_id=chat_id).order_by(Message.created_at.asc())
    total = msgs_q.count()
    items = msgs_q.offset((page - 1) * limit).limit(limit).all()

    messages = [{
        "id": m.id,
        "chat_id": m.chat_id,
        "sender": {
            "id": m.sender.id,
            "name": m.sender.full_name,
            "avatar": m.sender.profile_image,
        },
        "content": m.content,
        "sent_at": m.created_at.isoformat() + "Z",
        "is_read": m.is_read,
        "attachments": [],
    } for m in items]

    return success_response({
        "messages": messages,
        "pagination": {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        },
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


# -----------------------------------------------------------
# POST MESSAGE
# -----------------------------------------------------------
@bp.route("/<chat_id>/messages", methods=["POST"])
@jwt_required()
def post_message(chat_id):
    chat = Chat.query.get(chat_id)
    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    data = request.get_json() or {}
    content = data.get("content")
    if not content:
        return error_response("VALIDATION_ERROR", "content is required", 422)

    uid = get_jwt_identity()

    # sanitized = sanitize_message(content)
    msg = add_message(chat_id, uid, content)

    # --- Behavior analysis ---
    # history = Message.query.filter_by(chat_id=chat_id)\
    #     .order_by(Message.created_at.desc())\
    #     .limit(25).all()[::-1]

    # analysis = analyze_chat_behavior(history)
    warning = None

    # if analysis["risk"] in ("medium", "high"):
    #     chat.warning_active = True
    #     chat.warning_risk = analysis["risk"]
    #     chat.warning_message = (
    #         "We detected possible attempts to share contact or personal information. "
    #         "Continued violations may lead to account suspension."
    #     )
    #     chat.warning_expires_at = datetime.utcnow() + timedelta(days=7)
    #     chat.warning_for_user_id = uid

    #     db.session.commit()

    #     if chat.warning_active and chat.warning_for_user_id == uid:
    #         warning = {
    #             "risk": chat.warning_risk,
    #             "message": chat.warning_message,
    #             "expires_at": chat.warning_expires_at.isoformat() + "Z",
    #         }

    return success_response({
        "id": msg.id,
        "chat_id": msg.chat_id,
        "sender": {
            "id": msg.sender.id,
            "name": msg.sender.full_name,
            "avatar": msg.sender.profile_image,
        },
        "content": msg.content,
        "sent_at": msg.created_at.isoformat() + "Z",
        "is_read": msg.is_read,
        "warning": warning,
    })


# -----------------------------------------------------------
# EDIT MESSAGE
# -----------------------------------------------------------
@bp.route("/<chat_id>/messages/<message_id>", methods=["PUT"])
@jwt_required()
def edit_message(chat_id, message_id):
    chat = Chat.query.get(chat_id)
    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    uid = get_jwt_identity()
    data = request.get_json() or {}
    new_content = data.get("content", "").strip()

    if not new_content:
        return error_response("VALIDATION_ERROR", "content is required", 422)

    msg = Message.query.filter_by(id=message_id, chat_id=chat_id).first()
    if not msg:
        return error_response("NOT_FOUND", "Message not found", 404)

    if msg.sender_id != uid:
        return error_response("FORBIDDEN", "You can only edit your own messages", 403)

    msg.content = sanitize_message(new_content)
    msg.edited = True

    db.session.commit()

    # Behavior analysis again
    history = Message.query.filter_by(chat_id=chat_id)\
        .order_by(Message.created_at.desc())\
        .limit(25).all()[::-1]

    analysis = analyze_chat_behavior(history)
    warning = None

    if analysis["risk"] in ("medium", "high"):
        chat.warning_active = True
        chat.warning_risk = analysis["risk"]
        chat.warning_message = (
            "We detected possible attempts to share contact or personal information. "
            "Continued violations may lead to account suspension."
        )
        chat.warning_expires_at = datetime.utcnow() + timedelta(days=7)
        chat.warning_for_user_id = uid

        db.session.commit()

        if chat.warning_active and chat.warning_for_user_id == uid:
            warning = {
                "risk": chat.warning_risk,
                "message": chat.warning_message,
                "expires_at": chat.warning_expires_at.isoformat() + "Z",
            }

    return success_response({
        "message": {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "sender_id": msg.sender_id,
            "content": msg.content,
            "edited": msg.edited,
            "sent_at": msg.created_at.isoformat() + "Z",
        },
        "warning": warning,
    })


# -----------------------------------------------------------
# DELETE MESSAGE
# -----------------------------------------------------------
@bp.route("/<chat_id>/messages/<message_id>", methods=["DELETE"])
@jwt_required()
def delete_message(chat_id, message_id):
    chat = Chat.query.get(chat_id)
    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    uid = get_jwt_identity()

    msg = Message.query.filter_by(id=message_id, chat_id=chat_id).first()
    if not msg:
        return error_response("NOT_FOUND", "Message not found", 404)

    if msg.sender_id != uid:
        return error_response("FORBIDDEN", "You can only delete your own messages", 403)

    db.session.delete(msg)
    db.session.commit()

    return success_response({"deleted": True})


# -----------------------------------------------------------
# MARK ALL MESSAGES READ
# -----------------------------------------------------------
@bp.route("/<chat_id>/mark-read", methods=["POST"])
@jwt_required()
def mark_read(chat_id):
    uid = get_jwt_identity()

    updated = Message.query.filter(
        Message.chat_id == chat_id,
        Message.sender_id != uid,
        Message.is_read == False
    ).update({"is_read": True})

    db.session.commit()

    return success_response({"updated": bool(updated)})


# -----------------------------------------------------------
# CLEAR CHAT WARNING (admin)
# -----------------------------------------------------------
@bp.route("/<chat_id>/clear-warning", methods=["POST"])
@jwt_required()
def clear_chat_warning(chat_id):
    chat = Chat.query.get(chat_id)
    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    chat.warning_active = False
    chat.warning_risk = None
    chat.warning_message = None
    chat.warning_expires_at = None

    db.session.commit()

    return success_response({"cleared": True})


@bp.route("/<chat_id>", methods=["GET"])
@jwt_required()
def get_chat(chat_id):
    uid = get_jwt_identity()

    chat = Chat.query.filter(
        Chat.id == chat_id,
        (Chat.client_id == uid) | (Chat.writer_id == uid)
    ).first()

    if not chat:
        return error_response("NOT_FOUND", "Chat not found", 404)

    other_user = chat.writer if chat.client_id == uid else chat.client

    return success_response({
        "chat": {
            "id": chat.id,
            "order_id": chat.order_id,
            "order_title": chat.order.title if chat.order else None,
            "other_user": {
                "id": other_user.id,
                "name": other_user.full_name,
                "avatar": other_user.profile_image,
            },
            "warning": {
                "active": chat.warning_active,
                "risk": chat.warning_risk,
                "message": chat.warning_message,
            } if chat.warning_active else None,
            "unread_count": Message.query.filter(
                Message.chat_id == chat.id,
                Message.sender_id != uid,
                Message.is_read == False
            ).count()
        }
    })
