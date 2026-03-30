from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.models.bid import Bid
from app.models.order import Order
from app.models.user import User

from app.services.bid_service import place_bid
from app.services.notification_service import send_notification_to_user

from app.services.chat_service import (
    get_or_create_chat,
    add_message,
    sanitize_message
)

from app.extensions import db
from app.utils.response_formatter import success_response, error_response

from datetime import datetime, timezone
from sqlalchemy import or_, and_
from app.services.email_service import (
    send_bid_accepted_email
)

from app.services.wallet_service import (
    safe_debit_wallet,
    has_sufficient_balance,
    get_wallet_balance
)

bp = Blueprint("bids", __name__, url_prefix="/api/v1")

# ------------------------------------------------------------
#  GET /bids  —  List bids for current writer
# ------------------------------------------------------------
@bp.route("/bids", methods=["GET"])
@jwt_required()
def list_bids():
    user_id = get_jwt_identity()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 10))
    status = request.args.get("status")
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    now = datetime.now(timezone.utc)

    q = (
        Bid.query
        .join(Order, Order.id == Bid.order_id)
        .filter(
            Bid.user_id == user_id,
            Order.writer_id.is_(None),
            or_(
                Order.deadline.is_(None),
                Order.deadline >= now
            )
        )
    )

    # -------------------------
    # STATUS HANDLING (UPDATED)
    # -------------------------
    if status:
        if status == "unconfirmed":
            q = q.filter(
                or_(
                    Bid.status == "unconfirmed",
                    and_(
                        Order.updated_at.isnot(None),
                        Order.updated_at > Bid.submitted_at,
                        Bid.status != "cancelled",
                        Bid.status != "rejected"
                    )
                )
            )
        elif status == "declined":
            q = q.filter(Bid.status == "rejected")
        else:
            q = q.filter(Bid.status == status)

    # -------------------------
    # DATE FILTERS
    # -------------------------
    if date_from:
        try:
            df = datetime.fromisoformat(date_from)
            q = q.filter(Bid.submitted_at >= df)
        except ValueError:
            return error_response(
                "VALIDATION_ERROR",
                "Invalid date_from format (use ISO)",
                status=422
            )

    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            q = q.filter(Bid.submitted_at <= dt)
        except ValueError:
            return error_response(
                "VALIDATION_ERROR",
                "Invalid date_to format (use ISO)",
                status=422
            )

    total = q.count()
    items = (
        q.order_by(Bid.submitted_at.desc())
         .offset((page - 1) * limit)
         .limit(limit)
         .all()
    )

    viewer = User.query.get(user_id)

    bids = [
        b.serialize(viewer_role=viewer.role)
        for b in items
    ]

    pagination = {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }

    return success_response({"bids": bids, "pagination": pagination})

# ------------------------------------------------------------
#  GET /bids/<bid_id> — View single bid
# ------------------------------------------------------------
@bp.route("/bids/<bid_id>", methods=["GET"])
@jwt_required()
def get_bid(bid_id):
    uid = get_jwt_identity()
    now = datetime.now(timezone.utc)

    bid = (
        Bid.query
        .join(Order)
        .filter(
            Bid.id == bid_id,
            Bid.user_id == uid,
            Order.writer_id.is_(None),
            or_(
                Order.deadline.is_(None),
                Order.deadline >= now
            )
        )
        .first()
    )

    if not bid:
        return error_response("NOT_FOUND", "Bid not found", status=404)
    viewer = User.query.get(uid)

    return success_response(
        bid.serialize(viewer_role=viewer.role)
    )

# ------------------------------------------------------------
# POST /orders/<order_id>/bids — Place a bid
# ------------------------------------------------------------
@bp.route("/orders/<order_id>/bids", methods=["POST"])
@jwt_required()
def create_bid(order_id):
    data = request.get_json() or {}
    uid = get_jwt_identity()

    # Disallow custom deadlines
    if data.get("deadline"):
        return error_response(
            "VALIDATION_ERROR",
            "Custom deadlines are not allowed",
            422
        )

    # --- Validate bid amount ---
    try:
        writer_bid = float(data.get("amount"))
    except (TypeError, ValueError):
        return error_response(
            "VALIDATION_ERROR",
            "Bid amount must be numeric",
            {"field": "amount"},
            422
        )

    order = Order.query.get(order_id)
    if not order:
        return error_response("NOT_FOUND", "Order not found", 404)

    if order.writer_id or Bid.query.filter_by(order_id=order_id, status="accepted").first():
        return error_response(
            "INVALID_OPERATION",
            "This order already has an accepted bid",
            400
        )

    # --- Enforce writer-visible minimum ---
    if writer_bid < order.writer_budget:
        return error_response(
            "VALIDATION_ERROR",
            f"Minimum bid is {order.writer_budget}",
            {"field": "amount"},
            422
        )

    writer_pct = current_app.config["WRITER_PAYOUT_PERCENTAGE"]
    client_visible_bid = round(writer_bid / writer_pct, 2)

    # --- Create bid ---
    try:
        bid = place_bid(
            order_id=order.id,
            user_id=uid,
            writer_amount=writer_bid,
            client_amount=client_visible_bid,
            message=data.get("message"),
        )
    except ValueError as e:
        return error_response("INVALID_OPERATION", str(e), 400)
    except Exception as e:
        db.session.rollback()
        return error_response("SERVER_ERROR", str(e), 500)

    # --- Create or get chat ---
    chat = get_or_create_chat(order.id, order.client_id, uid)

    if data.get("message"):
        add_message(chat.id, uid, sanitize_message(data["message"]))

    payload = bid.serialize(viewer_role="writer")
    payload["chat_id"] = chat.id

    return success_response(payload, status=201)

# ------------------------------------------------------------
#  PUT /bids/<bid_id> — Update bid message or amount (if open)
# ------------------------------------------------------------
@bp.route("/bids/<bid_id>", methods=["PUT"])
@jwt_required()
def update_bid(bid_id):
    uid = get_jwt_identity()
    bid = Bid.query.filter_by(id=bid_id, user_id=uid).first()
    if not bid:
        return error_response("NOT_FOUND", "Bid not found", status=404)
    if bid.status != "open":
        return error_response(
            "INVALID_OPERATION",
            "Cannot modify bid after it’s closed",
            status=400
        )

    data = request.get_json() or {}
    bid_amount = data.get("amount", 0)
    message = data.get("message")

    viewer = User.query.get(uid)

    writer_pct = current_app.config["WRITER_PAYOUT_PERCENTAGE"]
    writer_amount = float(bid_amount)
    client_amount = round(writer_amount / writer_pct, 2)

    if bid_amount is not None:
        try:
            bid_amount = float(bid_amount)
        except ValueError:
            return error_response(
                "VALIDATION_ERROR",
                "Bid amount must be a number",
                status=422
            )

        order = Order.query.get(bid.order_id)
        if bid_amount < order.writer_budget:
            return error_response("VALIDATION_ERROR", "Bid cannot be less than order budget",
                                  {"field": "amount"}, status=422)

        bid.writer_amount = writer_amount
        bid.client_amount = client_amount

    if message is not None:
        bid.message = message

    # Updating submitted_at prevents the "Unconfirmed" label
    bid.submitted_at = datetime.utcnow()

    db.session.commit()
    return success_response(
        bid.serialize(viewer_role=viewer.role)
    )


# ------------------------------------------------------------
#  DELETE /bids/<bid_id> — Withdraw bid
# ------------------------------------------------------------
@bp.route("/bids/<bid_id>", methods=["DELETE"])
@jwt_required()
def withdraw_bid(bid_id):
    uid = get_jwt_identity()
    bid = Bid.query.filter_by(id=bid_id, user_id=uid).first()
    if not bid:
        return error_response("NOT_FOUND", "Bid not found", status=404)
    if bid.status != "open":
        return error_response("INVALID_OPERATION", "Only open bids can be withdrawn", status=400)

    bid.status = "cancelled"
    db.session.commit()
    return success_response({"message": "Bid withdrawn successfully"})


# ------------------------------------------------------------
#  GET /client/bids — List bids on client’s orders
# ------------------------------------------------------------
@bp.route("/client/bids", methods=["GET"])
@jwt_required()
def list_bids_for_client():
    client_id = get_jwt_identity()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 10))
    status = request.args.get("status")

    # Base: bids on the client's orders
    q = Bid.query.join(Order).filter(Order.client_id == client_id)

    # (1) Ignore cancelled bids
    q = q.filter(Bid.status != "cancelled")

    # (2) Hide bids on assigned orders unless accepted
    q = q.filter(
        (Order.writer_id.is_(None)) | (Bid.status == "accepted")
    )

    # Optional status filter
    if status:
        q = q.filter(Bid.status == status)

    total = q.count()
    bids = (
        q.order_by(Bid.submitted_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    serialized = []
    for b in bids:
        serialized.append(
            b.serialize(
                include_user_info=True,
                viewer_role="client"
            )
        )

    pagination = {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }

    return success_response({"bids": serialized, "pagination": pagination})


# ------------------------------------------------------------
#  PUT /client/bids/<bid_id>/status — Accept or reject a bid
# ------------------------------------------------------------
@bp.route("/client/bids/<bid_id>/status", methods=["PUT"])
@jwt_required()
def client_update_bid_status(bid_id):
    client_id = get_jwt_identity()
    data = request.get_json() or {}
    action = data.get("action")

    bid = (
        Bid.query.join(Order)
        .filter(Bid.id == bid_id, Order.client_id == client_id)
        .first()
    )

    if not bid:
        return error_response("NOT_FOUND", "Bid not found", status=404)

    derived_status = bid.get_derived_status()
    print(f"status = {derived_status}")

    if derived_status not in ["open", "pending"]:
        return error_response("INVALID_OPERATION", "Bid already processed", status=400)
    
    writer = bid.user

    # ------------------------------------------------
    # Process ACCEPT
    # ------------------------------------------------
    if action == "accept":
        if derived_status == "unconfirmed":
            return error_response(
                "INVALID_OPERATION",
                "Cannot accept an unconfirmed bid",
                status=400
            )

        # Prevent multiple accepted bids
        existing_accepted = (
            Bid.query.filter(
                Bid.order_id == bid.order_id,
                Bid.status == "accepted",
                Bid.id != bid.id,
            ).first()
        )

        if existing_accepted:
            return error_response(
                "ALREADY_ASSIGNED",
                "Another bid for this order has already been accepted.",
                status=409
            )

        order = bid.order

        if order.payment_status == "paid":
            return error_response(
                "ALREADY_PAID",
                "This order has already been paid.",
                status=400
            )

        required_amount = float(order.client_budget)

        # --- PRE-CHECK (clean + informative) ---
        if not has_sufficient_balance(client_id, required_amount):
            wallet_info = get_wallet_balance(client_id)

            return error_response(
                "INSUFFICIENT_FUNDS",
                (
                    f"Insufficient wallet balance. "
                    f"Required: ${required_amount}, "
                    f"Available: ${wallet_info['available_balance']}"
                ),
                status=402
            )

        # --- ACTUAL DEBIT (safe, transactional) ---
        try:
            safe_debit_wallet(
                user_id=client_id,
                amount=required_amount,
                tx_type="payment",
                description=f"Payment for order {order.id}",
                ref_type="order",
                ref_id=order.id
            )

            order.payment_status = "paid"
        except Exception:
            return error_response(
                "PAYMENT_ERROR",
                "Something went wrong while processing payment. Please try again.",
                status=500
            )

        # ---- Only after successful debit ----
        order.writer_budget = bid.writer_amount
        order.client_budget = bid.client_amount
        bid.status = "accepted"
        order.writer_id = bid.user_id
        order.status = "in_progress"

        # Reject all other bids
        other_bids = (
            Bid.query
            .filter(Bid.order_id == bid.order_id, Bid.id != bid.id)
            .filter(Bid.status.in_(["open", "pending"]))
            .all()
        )

        for b in other_bids:
            b.status = "rejected"

    # ------------------------------------------------
    # Process REJECT
    # ------------------------------------------------
    elif action == "reject":
        bid.status = "rejected"
        # send_bid_rejected_email(writer, bid.order)

    else:
        return error_response("VALIDATION_ERROR", "Invalid action (use 'accept' or 'reject')", status=422)

    db.session.commit()

    # -------------------------------------------------------------------
    # SEND NOTIFICATION TO WRITER (uses bid.user and bid.user_id)
    # -------------------------------------------------------------------
    if writer:
        if action == "accept":
            title = "Your Bid Was Accepted"
            message = (
                f"Your bid for {bid.order.id} ({bid.order.title}) has been accepted. "
                "You have been assigned as the writer."
            )

            

            send_notification_to_user(
                email=writer.email,
                title=title,
                message=message,
                notif_type="bid_update",
                details={
                    "order_id": bid.order_id,
                    "bid_id": bid.id,
                    "status": action,
                },
                sender_id=client_id,
            )

    return success_response({"message": f"Bid {action}ed successfully"})


# ------------------------------------------------------------
#  GET /client/orders/<order_id>/bids  — Bids for a specific order
# ------------------------------------------------------------
@bp.route("/client/orders/<order_id>/bids", methods=["GET"])
@jwt_required()
def list_bids_for_order(order_id):
    client_id = get_jwt_identity()

    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 10))
    status = request.args.get("status")

    # Ensure order belongs to the client
    order = Order.query.filter_by(id=order_id, client_id=client_id).first()
    if not order:
        return error_response("NOT_FOUND", "Order not found", status=404)

    q = Bid.query.filter(Bid.order_id == order_id)

    # Ignore cancelled
    q = q.filter(Bid.status != "cancelled")

    # Hide bids on assigned orders unless accepted
    if order.writer_id:
        q = q.filter(Bid.status == "accepted")

    # Status filter
    if status and status != "all":
        q = q.filter(Bid.status == status)

    total = q.count()

    bids = (
        q.order_by(Bid.submitted_at.desc())
         .offset((page - 1) * limit)
         .limit(limit)
         .all()
    )

    serialized = [
        b.serialize(
            include_user_info=True,
            viewer_role="client"
        )
        for b in bids
    ]

    pagination = {
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }

    return success_response({"bids": serialized, "pagination": pagination})

# ------------------------------------------------------------
#  PUT /bids/<bid_id>/confirm — Confirm updated order details
# ------------------------------------------------------------
@bp.route("/bids/<bid_id>/confirm", methods=["PUT"])
@jwt_required()
def confirm_bid(bid_id):
    uid = get_jwt_identity()

    bid = (
        Bid.query.join(Order)
            .filter(
                Bid.id == bid_id,
                Bid.user_id == uid,
                Order.writer_id.is_(None)  # Order not assigned yet
            )
            .first()
    )

    if not bid:
        return error_response("NOT_FOUND", "Bid not found", status=404)

    derived = bid.get_derived_status()
    if derived != "unconfirmed":
        return error_response(
            "INVALID_OPERATION",
            "This bid does not require confirmation",
            400
        )

    # Confirmation = acknowledge updated order details
    bid.submitted_at = datetime.utcnow()

    db.session.commit()
    return success_response({
        "message": "Bid successfully confirmed",
        "bid": bid.serialize()
    })
