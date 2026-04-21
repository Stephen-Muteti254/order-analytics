from flask import Blueprint, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models.user import User
from app.extensions import db
from app.utils.response_formatter import success_response, error_response

from sqlalchemy import func, desc
from app.models.review import Review
from app.models.order import Order
from app.models.writer_application import WriterApplication
from app.services.profile_service import build_leaderboard

import logging
from werkzeug.utils import secure_filename
import os, uuid
from datetime import datetime
from app.models.writer_profile import WriterProfile

from sqlalchemy import func


bp = Blueprint("profile", __name__, url_prefix="/api/v1/profile")


def is_writer_profile_complete(user: User) -> tuple[bool, list[str]]:
    if user.role != "writer":
        return True, []

    profile: WriterProfile | None = WriterProfile.query.filter_by(
        user_id=user.id
    ).first()

    if not profile:
        return False, [
            "bio",
            "profile_image",
            "specializations",
            "subjects",
            "education",
            "languages",
        ]

    missing = []

    if not profile.bio or len(profile.bio.strip()) < 100:
        missing.append("bio")

    if not profile.profile_image:
        missing.append("profile_image")

    if not profile.specializations:
        missing.append("specializations")

    if not profile.subjects:
        missing.append("subjects")

    if not profile.education:
        missing.append("education")

    if not profile.languages:
        missing.append("languages")

    return len(missing) == 0, missing


@bp.route("", methods=["GET"])
@jwt_required()
def get_profile():
    uid = get_jwt_identity()
    u = User.query.get(uid)

    if not u:
        return error_response("NOT_FOUND", "User not found", 404)

    profile = WriterProfile.query.filter_by(user_id=uid).first()

    # ---- Orders ----
    total_orders = db.session.query(func.count(Order.id))\
        .filter(Order.writer_id == uid)\
        .scalar() or 0

    completed_orders = db.session.query(func.count(Order.id))\
        .filter(
            Order.writer_id == uid,
            Order.status == "completed"
        ).scalar() or 0

    # ---- Earnings ----
    total_earnings = db.session.query(
        func.coalesce(func.sum(Order.writer_budget), 0)
    ).filter(
        Order.writer_id == uid,
        Order.status == "completed"
    ).scalar()

    # ---- Reviews ----
    avg_rating = db.session.query(
        func.coalesce(func.avg(Review.rating), 0)
    ).filter(
        Review.reviewee_id == uid
    ).scalar()

    success_rate = round(
        (completed_orders / total_orders * 100), 2
    ) if total_orders else 0

    is_complete, missing = is_writer_profile_complete(u)

    return success_response({
        "user": u.to_dict(),
        "writer_profile": profile.to_dict() if profile else {
            "profile_image": None,
            "bio": "",
            "specializations": [],
            "subjects": [],
            "education": [],
            "languages": [],
        },
        "metrics": {
            "rating": round(float(avg_rating), 2),
            "total_orders": total_orders,
            "completed_orders": completed_orders,
            "success_rate": success_rate,
            "earnings": float(total_earnings or 0),
        },
        "profile_completion": {
            "is_complete": is_complete,
            "missing_fields": missing
        }
    })


@bp.route("", methods=["PATCH"])
@jwt_required()
def patch_profile():
    uid = get_jwt_identity()
    data = request.get_json() or {}
    u = User.query.get(uid)
    if not u:
        return error_response("NOT_FOUND", "User not found", status=404)
    if "full_name" in data:
        u.full_name = data.get("full_name")
    if "bio" in data:
        u.bio = data.get("bio")
    db.session.commit()
    return success_response({"id": u.id, "full_name": u.full_name, "bio": u.bio, "updated_at": u.joined_at.isoformat() + "Z"})

@bp.route("/leaderboard", methods=["GET"])
@jwt_required()
def get_leaderboard():
    limit = int(request.args.get("limit", 50))
    results = build_leaderboard(limit)

    leaderboard = []
    for index, r in enumerate(results, start=1):

        completed = r.completed_orders or 0
        total = r.total_assigned or 0
        success_rate = round((completed / total) * 100, 2) if total > 0 else 0.0

        leaderboard.append({
            "rank": index,
            "id": r.id,
            "name": r.full_name,
            "avatar": r.profile_image,
            "rating": round(float(r.rating), 2),
            "ordersCompleted": completed,
            "successRate": success_rate,
            "specialization": r.specialization,
            "level": get_writer_level(index),
        })

    return success_response({"leaders": leaderboard})


@bp.route("/leaderboard/me", methods=["GET"])
@jwt_required()
def get_my_leaderboard_position():
    uid = get_jwt_identity()
    results = build_leaderboard()
    
    for index, r in enumerate(results, start=1):
        if r.id == uid:
            completed = r.completed_orders or 0
            total = r.total_assigned or 0
            success_rate = round((completed / total) * 100, 2) if total > 0 else 0.0
            return success_response({
                "id": r.id,
                "rank": index,
                "rating": round(float(r.rating), 2),
                "ordersCompleted": completed,
                "successRate": success_rate,
                "name": r.full_name,
            })

    return success_response({
        "rank": None,
        "rating": 0,
        "ordersCompleted": 0,
        "message": "User not ranked yet"
    })



def get_writer_level(rank: int) -> str | None:
    if rank == 1:
        return "Platinum Writer"
    if rank == 2:
        return "Gold Writer"
    if rank == 3:
        return "Silver Writer"
    return None


logger = logging.getLogger(__name__)


def save_profile_image(file, user_id):
    root = current_app.config.get("PROFILES_FOLDER")
    profile_root = os.path.join(root, str(user_id))
    os.makedirs(profile_root, exist_ok=True)

    filename = secure_filename(file.filename)
    unique = f"{uuid.uuid4().hex}_{filename}"
    path = os.path.join(profile_root, unique)
    file.save(path)

    return f"profiles/{user_id}/{unique}"


@bp.route("", methods=["PUT"])
@jwt_required()
def upsert_profile():
    uid = get_jwt_identity()
    user = User.query.get(uid)

    if not user:
        return error_response("NOT_FOUND", "User not found", 404)

    print(f"Profile update for user {uid}")
    print(f"Content-Type: {request.content_type}")


    print(f"request.content_type = {request.content_type}")
    print(f"request.files = {request.files}")
    print(f"request.form = {request.form}")

    logger.info("Uploaded file keys: %s", list(request.files.keys()))

    profile = WriterProfile.query.filter_by(user_id=uid).first()
    if not profile:
        profile = WriterProfile(
            id=str(uuid.uuid4()),
            user_id=uid,
            created_at=datetime.utcnow(),
        )
        db.session.add(profile)


    # ---- Handle file upload ----
    if request.files:
        logger.info("Files received: %s", list(request.files.keys()))

        image = request.files.get("profileImage")
        if image and image.filename:
            stored_path = save_profile_image(image, uid)
            profile.profile_image = stored_path
            user.profile_image = stored_path
        else:
            logger.warning("profileImage key missing or empty")

    # ---- Handle JSON payload ----
    else:
        data = request.get_json() or {}
        print(f"JSON payload: {data}")

        if "bio" in data:
            profile.bio = data["bio"]

        if "specializations" in data:
            profile.specializations = data["specializations"]

        if "subjects" in data:
            profile.subjects = data["subjects"]

        if "education" in data:
            profile.education = data["education"]

        if "languages" in data:
            profile.languages = data["languages"]

    # ---- Recompute completion ----
    is_complete, missing = is_writer_profile_complete(user)
    profile.is_complete = is_complete
    profile.profile_completion = round(
        (6 - len(missing)) / 6 * 100, 2
    )
    profile.updated_at = datetime.utcnow()

    db.session.commit()

    return success_response({
        "profile": {
            "id": profile.id,
            "profile_image": profile.profile_image,
            "bio": profile.bio,
            "specializations": profile.specializations,
            "subjects": profile.subjects,
            "education": profile.education,
            "languages": profile.languages,
        },
        "profile_completion": {
            "is_complete": is_complete,
            "missing_fields": missing,
            "percent": profile.profile_completion,
        }
    })

from flask import send_from_directory

@bp.route("/images/<path:filename>")
def serve_profile_image(filename):
    root = current_app.config["PROFILES_FOLDER"]
    # remove leading "profiles/" if present
    if filename.startswith("profiles/"):
        filename = filename[len("profiles/"):]
    return send_from_directory(root, filename)
