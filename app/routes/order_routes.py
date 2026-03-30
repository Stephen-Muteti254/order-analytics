import os
from datetime import timezone, datetime
from flask import Blueprint, request, send_file, current_app, url_for
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.extensions import db
from app.models.order import Order
from app.models.user import User
from app.models.review import Review
from app.models.declined_order import DeclinedOrder
from app.services.order_service import create_order, update_order_status
from app.utils.response_formatter import success_response, error_response
from app.utils.pagination import paginate_query
from app.models.order_invitation import OrderInvitation
from dateutil import parser
from app.models.bid import Bid
from app.services.notification_service import (
    send_notification_to_user,
    send_notification_to_user_without_email
)
from sqlalchemy import or_, cast
from sqlalchemy.types import String
from app.services.order_service import (
    save_uploaded_file,
    calculate_minimum_price,
    count_unpaid_unassigned_orders
)
from decimal import Decimal, ROUND_HALF_UP
from app.services.wallet_service import has_sufficient_balance
from sqlalchemy import func

from app.services.email_service import (
    send_order_cancelled_email,
    send_order_restored_email
)
from app.models.submission import Submission

def format_money(value):
    if value is None:
        return None
    return float(
        Decimal(value).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
    )


bp = Blueprint("orders", __name__, url_prefix="/api/v1/orders")


# ------------------------------------------------------------
#  GET /orders — List orders (clients see their orders; writers see marketplace or assigned)
# ------------------------------------------------------------
@bp.route("", methods=["GET"])
@jwt_required()
def list_orders():
    uid = get_jwt_identity()
    user = User.query.get(uid)
    if not user:
        return error_response("NOT_FOUND", "User not found", status=404)

    status = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 10, type=int)
    search = request.args.get("search")

    min_budget = request.args.get("min_budget", type=float)
    max_budget = request.args.get("max_budget", type=float)
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    assigned_to = request.args.get("assigned_to")

    q = Order.query

    # Exclude expired orders for writers
    if user.role == "writer" and assigned_to != "me":
        now = datetime.now(timezone.utc)
        q = q.filter(
            or_(
                Order.deadline == None,
                Order.deadline >= now
            )
        )

    # Role filtering - clients: only their orders
    if user.role == "client":
        q = q.filter_by(client_id=user.id)
        
        if status:
            if status == "in_progress":
                q = q.filter(Order.status.in_([
                    "in_progress",
                    "submitted_for_review",
                    "revision_requested"
                ]))
            elif status == "in-review":
                q = q.filter(Order.status == "submitted_for_review")
            elif status == "in-revision":
                q = q.filter(Order.status == "revision_requested")
            elif status == "completed":
                q = q.filter(Order.status == "completed")
            elif status == "cancelled":
                q = q.filter(Order.status == "cancelled")
    else:
        # If writer is fetching ONLY their assigned orders
        if assigned_to == "me":
            q = q.filter(Order.writer_id == user.id)

            # q = q.filter(Order.payment_status == "paid")

            if status:
                if status == "in-progress":
                    q = q.filter(Order.status.in_([
                        "in_progress",
                        "submitted_for_review",
                        "revision_requested"
                    ]))
                elif status == "in-progress-only":
                    q = q.filter(Order.status == "in_progress")
                elif status == "in-review":
                    q = q.filter(Order.status == "submitted_for_review")
                elif status == "in-revision":
                    q = q.filter(Order.status == "revision_requested")
                elif status == "completed":
                    q = q.filter(Order.status == "completed")
                elif status == "cancelled":
                    q = q.filter(Order.status == "cancelled")

        # Otherwise -> writer browsing marketplace
        else:
            # --------------------------------------------------
            # Exclude orders the writer has already bid on
            # --------------------------------------------------
            if user.role == "writer":
                writer_bid_order_ids = (
                    db.session.query(Bid.order_id)
                    .filter(Bid.user_id == user.id)
                )
                q = q.filter(~Order.id.in_(writer_bid_order_ids))

            # Exclude orders they've declined
            declined_ids = db.session.query(DeclinedOrder.order_id).filter_by(writer_id=user.id)
            if status == "declined":
                q = q.filter(Order.id.in_(declined_ids))
            else:
                q = q.filter(~Order.id.in_(declined_ids))

            # Exclude orders with accepted bids (assigned orders)
            accepted_bid_order_ids = db.session.query(Bid.order_id).filter(Bid.status == "accepted")
            if user.role != "client":
                q = q.filter(~Order.id.in_(accepted_bid_order_ids))

            # Handle invited orders
            if status == "invited":
                invited_ids = db.session.query(OrderInvitation.order_id).filter_by(writer_id=user.id)
                q = q.filter(Order.id.in_(invited_ids))

            # Regular status filter
            if status and status not in ["invited", "declined"]:
                q = q.filter_by(status=status)

    # Search filter
    if search:
        search_term = f"%{search}%"
        q = q.filter(
            or_(
                cast(Order.id, String).ilike(search_term),
                Order.title.ilike(search_term),
                Order.subject.ilike(search_term),
                Order.description.ilike(search_term),
                Order.status.ilike(search_term)
            )
        )

    # Budget/date filters
    if min_budget is not None:
        q = q.filter(
            Order.writer_budget >= min_budget
            if user.role == "writer"
            else Order.client_budget >= min_budget
        )

    if max_budget is not None:
        q = q.filter(
            Order.writer_budget <= max_budget
            if user.role == "writer"
            else Order.client_budget <= max_budget
        )

    if date_from:
        try:
            d_from = parser.parse(date_from)
            q = q.filter(Order.created_at >= d_from)
        except:
            pass
    if date_to:
        try:
            d_to = parser.parse(date_to)
            q = q.filter(Order.created_at <= d_to)
        except:
            pass

    # Pagination & serialization
    items, pagination = paginate_query(q.order_by(Order.created_at.desc()), page, limit)
    orders = []
    for o in items:
        orders.append({
            "id": o.id,
            "title": o.title,
            "subject": o.subject,
            "type": o.type,
            "pages": o.pages,
            "deadline": o.deadline.astimezone(timezone.utc).isoformat() if o.deadline else None,
            "budget": format_money(
                o.writer_budget
                if user.role == "writer"
                else o.client_budget
            ),
            "status": o.status,
            "client": {
                "id": o.client.id,
                "name": o.client.full_name,
                "country": o.client.country,
                "avatar": o.client.profile_image
            } if o.client else None,
            "created_at": o.created_at.isoformat() + "Z" if o.created_at else None,
            "writer_assigned": o.writer_id is not None,
            "citation_style": o.citation_style,
            "format": o.format,
            "language": o.language,
        })

    return success_response({"orders": orders, "pagination": pagination})


# ------------------------------------------------------------
#  Helper: serialize_order(order) — returns order details + files + writer_assigned
# ------------------------------------------------------------
def serialize_order(order, viewer):
    data = {
        "id": order.id,
        "title": order.title,
        "subject": order.subject,
        "type": order.type,
        "pages": order.pages,
        "deadline": order.deadline.astimezone(timezone.utc).isoformat() if order.deadline else None,

        # CRITICAL: role-based budget exposure
        "budget": format_money(
            order.writer_budget
            if viewer.role == "writer"
            else order.client_budget
        ),
        "status": order.status,
        "description": order.description,
        "requirements": order.requirements,
        "created_at": order.created_at.isoformat() + "Z" if order.created_at else None,
        "client_id": order.client_id,
        "writer_id": order.writer_id,
        "writer_assigned": order.writer_id is not None,
        "citation_style": order.citation_style,
        "format": order.format,
        "language": order.language,
    }

    # ADD THIS
    if order.client and viewer.role == "writer":
        data["client"] = {
            # "id": order.client.id,
            # "name": order.client.full_name,
            "country": order.client.country,
            # "avatar": order.client.profile_image,
        }

    data["preferred_writers"] = [
        {
            "id": inv.writer.id,
            "name": inv.writer.full_name,
            "avatar": inv.writer.profile_image,
        }
        for inv in order.invitations
    ]

    # Attach file URLs
    root_dir = current_app.config.get("ORDERS_FOLDER", "uploads/orders")
    order_dir = os.path.join(root_dir, str(order.client_id), order.id)

    if os.path.exists(order_dir):
        data["files"] = [
            url_for(
                "orders.get_order_file",
                order_id=order.id,
                filename=f,
                _external=True
            )
            for f in os.listdir(order_dir)
        ]
    else:
        data["files"] = []

    return data


# ------------------------------------------------------------
#  GET /orders/<order_id> — Get single order details
# ------------------------------------------------------------
from datetime import datetime, timezone

@bp.route("/<order_id>", methods=["GET"])
@jwt_required()
def get_order(order_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)

    if not user:
        return error_response("NOT_FOUND", "User not found", status=404)

    order = Order.query.get(order_id)
    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)

    # Prevent writers from accessing expired orders
    if user.role == "writer" and order.deadline:
        # Make deadline UTC-aware if it is naive
        if order.deadline.tzinfo is None:
            deadline_utc = order.deadline.replace(tzinfo=timezone.utc)
        else:
            deadline_utc = order.deadline

    return success_response(serialize_order(order, user))


# ------------------------------------------------------------
#  POST /orders — Create new order (form-data or json)
# ------------------------------------------------------------
@bp.route("", methods=["POST"])
@jwt_required()
def create_new_order():
    uid = get_jwt_identity()
    user = User.query.get(uid)
    
    if not user:
        return error_response("NOT_FOUND", "User not found", status=404)

    # Detect content type
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        form_data = request.form.to_dict()
        files = request.files
    else:
        form_data = request.get_json(silent=True) or {}
        files = None

    required = ["title", "category", "orderType", "deadline", "budget"]
    missing = [r for r in required if not form_data.get(r)]
    if missing:
        return error_response("VALIDATION_ERROR", "Missing fields", {"fields": missing}, status=422)

    # --- Extract fields ---
    title = form_data.get("title")
    category = form_data.get("category")
    order_type = form_data.get("orderType")
    pages = int(form_data.get("pages") or 1)
    deadline = form_data.get("deadline")
    
    client_budget = float(form_data.get("budget", 0))
    writer_pct = current_app.config["WRITER_PAYOUT_PERCENTAGE"]
    writer_budget = round(client_budget * writer_pct, 2)

    
    deadline_str = form_data.get("deadline")

    deadline_utc = None
    
    if deadline_str:
        parsed = parser.isoparse(deadline_str)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        deadline_utc = parsed.astimezone(timezone.utc)

    form_data["deadline"] = deadline_utc


    # --- Calculate minimum allowed budget ---
    now_utc = datetime.now(timezone.utc)

    min_budget = calculate_minimum_price(
        category=category,
        order_type=order_type,
        pages=pages,
        deadline=deadline_utc,
        now=now_utc
    )

    if client_budget < min_budget:
        return error_response(
            "BUDGET ERROR",
            f"Budget too low. Minimum allowed is {min_budget}",
            status=400
        )

    # --------------------------------------------------
    # FREE ORDER THRESHOLD LOGIC (NEW)
    # --------------------------------------------------
    FREE_ORDER_LIMIT = current_app.config.get("FREE_ORDER_LIMIT", 2)

    unpaid_unassigned_count = count_unpaid_unassigned_orders(uid)

    # Case 1: User is BELOW limit → allow without balance check
    if unpaid_unassigned_count < FREE_ORDER_LIMIT:
        pass  # allow creation freely

    # Case 2: User reached/exceeded limit → enforce balance
    else:
        if not has_sufficient_balance(uid, min_budget):
            return error_response(
                "ORDER_LIMIT_REACHED",
                (
                    f"You already have {unpaid_unassigned_count} active unpaid orders. "
                    f"To create more orders, you need at least ${min_budget} in your wallet."
                ),
                status=400
            )

    try:
        pages = int(form_data.get("pages", 1))
    except ValueError:
        pages = 1

    try:
        preferred_writers = []
        for key, value in form_data.items():
            if key.startswith("preferred_writers[") and value and value.strip():
                preferred_writers.append(value.strip())

        form_data["min_budget"] = min_budget
        form_data["client_budget"] = client_budget
        form_data["writer_budget"] = writer_budget
        
        if files:
            order = create_order(user, form_data, files)
        else:
            order = create_order(user, form_data)

        if preferred_writers:
            invited = []
            for w in preferred_writers:
                writer = User.query.filter(
                    (User.id == w) | (User.full_name.ilike(f"%{w}%"))
                ).first()
                if writer and writer.role == "writer":
                    inv = OrderInvitation(order_id=order.id, writer_id=writer.id)
                    db.session.add(inv)
                    invited.append(writer.full_name)
            db.session.commit()
            print(f"[ORDER_INVITE] Invited writers: {invited}")

        return success_response({
            "id": order.id,
            "title": order.title,
            "status": order.status,
            "created_at": order.created_at.isoformat() + "Z"
        }, status=200)
    except Exception as e:
        db.session.rollback()
        print(f"[ORDER_CREATE_ERROR] {str(e)}")
        return error_response("ORDER_CREATE_ERROR", str(e), status=400)


# ------------------------------------------------------------
#  PATCH /orders/<order_id> — Update order (NOT allowed once assigned)
# ------------------------------------------------------------
@bp.route("/<order_id>", methods=["PATCH"])
@jwt_required()
def patch_order(order_id):
    uid = get_jwt_identity()

    order = Order.query.get(order_id)
    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)

    # --------------------------------------------------
    # Business rule: cannot edit assigned orders
    # --------------------------------------------------
    if order.writer_id is not None:
        return error_response(
            "FORBIDDEN",
            "This order has already been assigned and cannot be edited.",
            status=403,
        )

    # --------------------------------------------------
    # Parse payload
    # --------------------------------------------------
    is_multipart = (
        request.content_type
        and request.content_type.startswith("multipart/form-data")
    )

    if is_multipart:
        data = request.form.to_dict()
        files = request.files.getlist("attachedFiles")
        existing_files = request.form.getlist("existingFiles")
    else:
        data = request.get_json(silent=True) or {}
        files = []
        existing_files = []

    existing_filenames = {os.path.basename(f) for f in existing_files}

    # --------------------------------------------------
    # Frontend → backend field mapping
    # --------------------------------------------------
    field_map = {
        "category": "subject",
        "orderType": "type",
        "detailedRequirements": "detailed_requirements",
        "additionalNotes": "additional_notes",
        "citationStyle": "citation_style",
    }

    normalized = {
        field_map.get(k, k): v for k, v in data.items()
    }

    # --------------------------------------------------
    # Editable fields ONLY
    # --------------------------------------------------
    editable_fields = {
        "title",
        "subject",
        "type",
        "pages",
        "description",
        "requirements",
        "detailed_requirements",
        "additional_notes",
        "deadline",
        "format",
        "citation_style",
        "language"
    }

    updates = {
        k: v for k, v in normalized.items() if k in editable_fields
    }

    # --------------------------------------------------
    # Type coercion & validation
    # --------------------------------------------------
    if "pages" in updates:
        try:
            updates["pages"] = max(1, int(updates["pages"]))
        except (TypeError, ValueError):
            return error_response(
                "VALIDATION_ERROR",
                "Invalid pages value",
                status=422,
            )

    if "deadline" in updates and updates["deadline"]:
        try:
            parsed = parser.isoparse(updates["deadline"])
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            updates["deadline"] = parsed.astimezone(timezone.utc)
        except Exception:
            return error_response(
                "VALIDATION_ERROR",
                "Invalid deadline format",
                status=422,
            )

    # --------------------------------------------------
    # Determine if pricing needs recalculation
    # --------------------------------------------------
    pricing_fields = {"subject", "type", "pages", "deadline"}
    pricing_changed = any(f in updates for f in pricing_fields)

    if pricing_changed:
        now_utc = datetime.now(timezone.utc)

        new_subject = updates.get("subject", order.subject)
        new_type = updates.get("type", order.type)
        new_pages = updates.get("pages", order.pages)
        new_deadline = updates.get("deadline", order.deadline)

        new_min_budget = calculate_minimum_price(
            category=new_subject,
            order_type=new_type,
            pages=new_pages,
            deadline=new_deadline,
            now=now_utc,
        )

        # If client supplied new budget, validate it
        if "budget" in data:
            try:
                client_budget = float(data["budget"])
            except ValueError:
                return error_response(
                    "VALIDATION_ERROR",
                    "Invalid budget value",
                    status=422,
                )

            if client_budget < new_min_budget:
                return error_response(
                    "BUDGET_ERROR",
                    f"Minimum allowed budget for your order is {new_min_budget}",
                    status=400,
                )

            if not has_sufficient_balance(uid, new_min_budget):
                return error_response(
                    "INSUFFICIENT_WALLET_BALANCE",
                    f"Your wallet balance is insufficient for the updated order requirements. Minimum required is {new_min_budget}.",
                    status=400
                )

        else:
            client_budget = float(order.client_budget)

            if client_budget < new_min_budget:
                return error_response(
                    "BUDGET_ERROR",
                    f"Order requires a minimum budget of {new_min_budget}",
                    status=400,
                )

        writer_pct = current_app.config["WRITER_PAYOUT_PERCENTAGE"]
        writer_budget = round(client_budget * writer_pct, 2)

        order.client_budget = client_budget
        order.writer_budget = writer_budget
        order.minimum_allowed_budget = new_min_budget

    # --------------------------------------------------
    # Apply attribute updates
    # --------------------------------------------------
    for field, value in updates.items():
        setattr(order, field, value)

    # --------------------------------------------------
    # File handling
    # --------------------------------------------------
    root_dir = current_app.config.get("ORDERS_FOLDER", "uploads/orders")
    order_dir = os.path.join(root_dir, str(order.client_id), order.id)
    os.makedirs(order_dir, exist_ok=True)

    # Remove deleted files
    for fname in os.listdir(order_dir):
        if fname not in existing_filenames:
            try:
                os.remove(os.path.join(order_dir, fname))
            except OSError:
                pass

    # Save new uploads
    for file in files:
        if file and file.filename:
            save_uploaded_file(file, order_dir)

    attachments = os.listdir(order_dir) if os.path.exists(order_dir) else []
    base_text = (order.requirements or "").split("\n\n[Attachments:")[0]

    if attachments:
        order.requirements = (
            f"{base_text}\n\n"
            f"[Attachments: {len(attachments)} file(s)]\n"
            + "\n".join(attachments)
        )
    else:
        order.requirements = base_text

    db.session.commit()

    # --------------------------------------------------
    # Serialize response
    # --------------------------------------------------
    file_urls = [
        url_for(
            "orders.get_order_file",
            order_id=order.id,
            filename=f,
            _external=True,
        )
        for f in attachments
    ]

    return success_response(
        {
            "order": {
                "id": order.id,
                "title": order.title,
                "category": order.subject,
                "orderType": order.type,
                "pages": order.pages,
                "clientBudget": float(order.client_budget),
                "writerBudget": float(order.writer_budget),
                "minimumAllowedBudget": order.minimum_allowed_budget,
                "deadline": (
                    order.deadline.astimezone(timezone.utc).isoformat()
                    if order.deadline
                    else None
                ),
                "description": order.description,
                "requirements": order.requirements,
                "additionalNotes": order.additional_notes,
                "attachments": file_urls,
                "status": order.status,
                "progress": order.progress,
                "updatedAt": (
                    order.updated_at.isoformat() + "Z"
                    if order.updated_at
                    else None
                ),
            },
            "message": "Order updated successfully",
        }
    )


# ------------------------------------------------------------
#  POST /orders/<order_id>/decline — Writer declines order (record)
# ------------------------------------------------------------
@bp.route("/<order_id>/decline", methods=["POST"])
@jwt_required()
def decline_order(order_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    order = Order.query.get(order_id)

    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)
    if user.role == "client":
        return error_response("FORBIDDEN", "Clients cannot decline orders", status=403)

    from app.models.declined_order import DeclinedOrder
    existing = DeclinedOrder.query.filter_by(order_id=order.id, writer_id=user.id).first()
    if existing:
        return error_response(
            "ALREADY_DECLINED",
            "You have already declined this order",
            status=400
        )

    try:
        data = request.get_json(silent=True) or {}
    except:
        data = {}

    reason = data.get("reason", "")

    declined = DeclinedOrder(order_id=order.id, writer_id=user.id, reason=reason)
    db.session.add(declined)
    db.session.commit()

    return success_response({
        "message": "Order declined successfully",
        "order_id": order.id,
        "status": "declined"
    })


# ------------------------------------------------------------
#  GET /orders/files/<order_id>/<filename> — Download attached file
# ------------------------------------------------------------
@bp.route("/files/<order_id>/<filename>", methods=["GET"])
@jwt_required()
def get_order_file(order_id, filename):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    order = Order.query.get(order_id)

    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)

    root_dir = current_app.config.get("ORDERS_FOLDER", "uploads/orders")
    file_path = os.path.join(root_dir, str(order.client_id), order.id, filename)

    if not os.path.exists(file_path):
        return error_response("NOT_FOUND", "File not found", status=404)

    return send_file(file_path, as_attachment=True)


# ------------------------------------------------------------
#  POST /orders/<order_id>/cancel — Cancel an order (client only)
#  - If a writer is assigned, client must provide a reason
#  - Notify writer when assigned order is cancelled
# ------------------------------------------------------------
@bp.route("/<order_id>/cancel", methods=["POST"])
@jwt_required()
def cancel_order(order_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    order = Order.query.get(order_id)

    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)

    if user.role != "client" or order.client_id != user.id:
        return error_response(
            "FORBIDDEN",
            "Only the client who created the order can cancel it",
            status=403
        )

    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()

    # If a writer is assigned, reason required
    if order.writer_id and not reason:
        return error_response(
            "REASON_REQUIRED",
            "A cancellation reason is required because a writer has already been assigned.",
            status=400
        )

    reason = reason or "Cancelled by client"

    order.status = "cancelled"
    order.updated_at = datetime.utcnow()
    db.session.commit()

    # Notify writer if assigned
    if order.writer_id:
        writer = User.query.get(order.writer_id)
        if writer:
            send_notification_to_user_without_email(
                email=writer.email,
                title="Order Cancelled",
                message=f"The client has cancelled order {order.id}. Reason: {reason}",
                notif_type="order_cancelled",
                details={
                    "order_id": order.id,
                    "reason": reason
                },
                sender_id=order.client_id,
            )

            send_order_cancelled_email(writer, order, reason)

    return success_response({
        "orderId": order.id,
        "status": order.status,
        "message": "Order cancelled successfully",
        "cancelReason": reason,
        "updatedAt": order.updated_at.isoformat() + "Z"
    })



# ------------------------------------------------------------
#  POST /orders/<order_id>/restore — Restore a cancelled order
# ------------------------------------------------------------
@bp.route("/<order_id>/restore", methods=["POST"])
@jwt_required()
def restore_order(order_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    order = Order.query.get(order_id)

    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)

    if user.role != "client" or order.client_id != user.id:
        return error_response(
            "FORBIDDEN",
            "Only the client who created the order can restore it",
            status=403
        )

    if order.status != "cancelled":
        return error_response(
            "INVALID_STATE",
            "Only cancelled orders can be restored",
            status=400
        )

    # Check if submissions existed before cancellation
    has_submissions = (
        db.session.query(Submission.id)
        .filter(Submission.order_id == order.id)
        .first()
        is not None
    )

    restored_status = (
        "submitted_for_review" if has_submissions else "in_progress"
    )

    order.status = restored_status
    order.updated_at = datetime.utcnow()
    db.session.commit()

    # Notify writer if assigned
    if order.writer_id:
        writer = User.query.get(order.writer_id)
        if writer:
            send_notification_to_user_without_email(
                email=writer.email,
                title="Order Restored",
                message=(
                    f"The client has restored order {order.title}. "
                    "You may continue working on it."
                ),
                notif_type="order_restored",
                details={
                    "order_id": order.id,
                    "restored_status": restored_status
                },
                sender_id=order.client_id,
            )

            send_order_restored_email(writer, order, restored_status)

    return success_response({
        "orderId": order.id,
        "status": order.status,
        "message": "Order restored successfully",
        "updatedAt": order.updated_at.isoformat() + "Z"
    })



# ------------------------------------------------------------
#  POST /orders/pricing/preview — Pricing preview helper
# ------------------------------------------------------------
@bp.route("/pricing/preview", methods=["POST"])
@jwt_required(optional=True)
def preview_pricing():
    data = request.json or {}
    category = data.get("category")
    order_type = data.get("orderType")
    pages = data.get("pages")

    deadline_utc = None
    if data.get("deadline"):
        parsed = parser.isoparse(data["deadline"])
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        deadline_utc = parsed.astimezone(timezone.utc)

    min_budget = calculate_minimum_price(
        category,
        order_type,
        pages,
        deadline_utc,
        datetime.now(timezone.utc)
    )

    return success_response({"min_budget": min_budget})


@bp.route("/<order_id>/review", methods=["POST"])
@jwt_required()
def review_writer(order_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)

    if not user or user.role != "client":
        return error_response("FORBIDDEN", "Only clients can submit reviews", 403)

    order = Order.query.get_or_404(order_id)

    if order.client_id != user.id:
        return error_response("FORBIDDEN", "You do not own this order", 403)

    # Allow review if:
    # 1. Order completed
    # 2. Order cancelled but writer was assigned
    can_review = (
        order.status == "completed"
        or (order.status == "cancelled" and order.writer_id is not None)
    )

    if not can_review:
        return error_response(
            "INVALID_STATE",
            "You can only review completed orders or cancelled orders that had an assigned writer",
            400,
        )

    if not order.writer_id:
        return error_response("INVALID_STATE", "This order has no writer to review", 400)

    if Review.query.filter_by(order_id=order.id).first():
        return error_response("DUPLICATE", "You have already reviewed this order", 400)

    data = request.get_json() or {}
    rating = data.get("rating")
    review_text = data.get("review")

    if not isinstance(rating, int) or rating < 1 or rating > 5:
        return error_response(
            "VALIDATION_ERROR",
            "Rating must be an integer between 1 and 5",
            400,
        )

    try:
        print(f"[REVIEW] Client {user.id} reviewing writer {order.writer_id} for order {order.id}")

        # Add review
        review = Review(
            order_id=order.id,
            reviewer_id=user.id,
            reviewee_id=order.writer_id,
            rating=rating,
            review=review_text
        )

        db.session.add(review)
        db.session.flush()

        writer = User.query.get(order.writer_id)

        # Recalculate writer rating
        avg_rating = db.session.query(func.avg(Review.rating))\
            .filter(Review.reviewee_id == writer.id)\
            .scalar()

        writer.rating = round(avg_rating or 0, 2)

        # Only increment completed orders if order was actually completed
        if order.status == "completed":
            writer.completed_orders += 1

        db.session.commit()

        print(f"[REVIEW SUCCESS] Order {order.id} review stored")

        return success_response({"message": "Review submitted successfully"})

    except Exception as e:
        db.session.rollback()
        print(f"[REVIEW ERROR] Failed to submit review for order {order.id}: {str(e)}")

        return error_response(
            "REVIEW_FAILED",
            "Failed to submit review",
            500
        )

@bp.route("/<order_id>/has_review", methods=["GET"])
@jwt_required()
def has_review(order_id):
    uid = get_jwt_identity()
    user = User.query.get(uid)
    
    order = Order.query.get_or_404(order_id)
    
    if user.role != "client" or order.client_id != user.id:
        return error_response("FORBIDDEN", "Access denied", 403)
    
    review_exists = Review.query.filter_by(order_id=order.id).first() is not None
    return success_response({"isSubmitted": review_exists})
