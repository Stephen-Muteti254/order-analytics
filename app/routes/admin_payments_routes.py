from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import or_

from app.extensions import db
from app.models.user import User
from app.models.wallet import Wallet
from app.models.wallet_transaction import WalletTransaction
from app.models.withdrawal_request import WithdrawalRequest
from app.utils.response_formatter import success_response, error_response
from app.services.notification_service import (
    send_notification_to_user,
    send_notification_to_user_without_email
)
import uuid
from datetime import timezone, datetime
from app.services.email_service import (
    send_withdrawal_paid_email,
    send_withdrawal_rejected_email
)

bp = Blueprint("admin_payments", __name__, url_prefix="/api/v1/admin")


def gen_uuid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def require_admin():
    uid = get_jwt_identity()
    user = User.query.get(uid)

    if not user or not user.role.lower() in ("admin", "super_admin"):
        return None, error_response("FORBIDDEN", "Admin access required", status=403)

    return user, None


# ==========================================================
#  GET /admin/withdrawals
#  Filters:
#    page, limit
#    status=pending|approved|rejected
#    search (writer name or email)
# ==========================================================
@bp.route("/withdrawals", methods=["GET"])
@jwt_required()
def admin_list_withdrawals():
    admin, err = require_admin()
    if err:
        return err

    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    status = request.args.get("status")
    search = request.args.get("search")

    if status == 'approved':
        status = 'paid'

    q = WithdrawalRequest.query.join(User)

    if status:
        q = q.filter(WithdrawalRequest.status == status)

    if search:
        q = q.filter(
            or_(
                User.full_name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%")
            )
        )

    total = q.count()
    items = (
        q.order_by(WithdrawalRequest.requested_at.desc())
         .offset((page - 1) * limit)
         .limit(limit)
         .all()
    )

    return success_response({
        "withdrawals": [
            {
                "id": w.id,
                "amount": float(w.amount),
                "status": w.status,
                "method": w.method,
                "destination": w.destination,
                "requested_at": w.requested_at.isoformat() + "Z",
                "writer": {
                    "id": w.user.id,
                    "name": w.user.full_name,
                    "email": w.user.email,
                    "avatar": w.user.profile_image,
                }
            }
            for w in items
        ],
        "pagination": {
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    })


# ==========================================================
#  PATCH /admin/withdrawals/<id>/approve
# ==========================================================
@bp.route("/withdrawals/<wid>/approve", methods=["PATCH"])
@jwt_required()
def admin_approve_withdrawal(wid):
    admin, err = require_admin()
    if err:
        return err

    wr = WithdrawalRequest.query.get(wid)
    if not wr:
        return error_response("NOT_FOUND", "Withdrawal not found", 404)

    if wr.status != "pending":
        return error_response("INVALID_STATE", "Only pending withdrawals can be approved", 400)

    wallet = Wallet.query.filter_by(user_id=wr.user_id).with_for_update().first()
    if not wallet or wallet.balance < wr.amount:
        return error_response("INSUFFICIENT_FUNDS", "Wallet balance mismatch", 400)

    # Ledger entry
    tx = WalletTransaction(
        id=gen_uuid("txn"),
        wallet_id=wallet.id,
        amount=-wr.amount,
        type="withdrawal",
        reference_type="withdrawal",
        reference_id=wr.id,
        description="Withdrawal payout"
    )

    wallet.balance -= wr.amount
    wr.status = "paid"
    wr.processed_at = datetime.utcnow()

    db.session.add(tx)
    db.session.commit()

    send_notification_to_user_without_email(
        email=wr.user.email,
        title="Withdrawal Paid",
        message=f"Your withdrawal of ${wr.amount:.2f} has been processed.",
        notif_type="success"
    )

    send_withdrawal_paid_email(wr.user, wr.amount)

    return success_response({"message": "Withdrawal approved and paid"})


# ==========================================================
#  PATCH /admin/withdrawals/<id>/reject
# ==========================================================
@bp.route("/withdrawals/<wid>/reject", methods=["PATCH"])
@jwt_required()
def admin_reject_withdrawal(wid):
    admin, err = require_admin()
    if err:
        return err

    wr = WithdrawalRequest.query.get(wid)
    if not wr:
        return error_response("NOT_FOUND", "Withdrawal not found", 404)

    if wr.status != "pending":
        return error_response("INVALID_STATE", "Only pending withdrawals can be rejected", 400)

    data = request.get_json() or {}
    reason = data.get("reason")

    wr.status = "rejected"
    db.session.commit()

    send_notification_to_user_without_email(
        email=wr.user.email,
        title="Withdrawal Rejected",
        message=(
            f"Your withdrawal of ${wr.amount:.2f} was rejected"
            f"{f': {reason}' if reason else '.'}"
        ),
        notif_type="error"
    )

    send_withdrawal_rejected_email(wr.user, wr.amount, reason)

    return success_response({"message": "Withdrawal rejected"})
