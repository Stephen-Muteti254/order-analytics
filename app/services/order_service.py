from app.extensions import db
from app.models.order import Order
from datetime import timezone, datetime
from flask import current_app, url_for, send_file, jsonify
from werkzeug.utils import secure_filename
import os, uuid


def save_uploaded_file(file, upload_dir):
    """Helper to securely save an uploaded file and return filename + path."""
    os.makedirs(upload_dir, exist_ok=True)
    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(upload_dir, unique_name)
    file.save(file_path)
    return unique_name, file_path


def count_unpaid_unassigned_orders(user_id):
    return (
        db.session.query(Order)
        .filter(
            Order.client_id == user_id,
            Order.writer_id.is_(None),
            Order.payment_status == "unpaid"
        )
        .count()
    )


def create_order(user, form_data, files=None):
    order_id = f"ORD-{uuid.uuid4().hex[:8]}"
    order = Order(
        id=order_id,
        title=form_data.get("title"),
        subject=form_data.get("category"),
        type=form_data.get("orderType"),
        pages=int(form_data.get("pages") or 1),
        deadline=form_data.get("deadline"),
        client_budget=form_data.get("client_budget", 0),
        writer_budget=form_data.get("writer_budget", 0),
        description=form_data.get("description"),
        requirements=form_data.get("requirements"),
        detailed_requirements=form_data.get("detailedRequirements"),
        additional_notes=form_data.get("additionalNotes"),
        format=form_data.get("format", "PDF"),
        citation_style=form_data.get("citationStyle", "APA"),
        language=form_data.get("language", "en-us"),
        tags=form_data.get("tags") or [],
        minimum_allowed_budget=form_data.get("min_budget", 0),
        client_id=user.id,
        created_at=datetime.utcnow(),
    )

    db.session.add(order)
    db.session.commit()

    # --- Handle file uploads ---
    saved_files = []
    if files:
        root_dir = current_app.config.get("ORDERS_FOLDER", "uploads/orders")
        order_dir = os.path.join(root_dir, str(user.id), order.id)

        for file in files.getlist("attachedFiles"):
            if not file or not file.filename:
                continue
            fname, fpath = save_uploaded_file(file, order_dir)
            saved_files.append(fname)

    if saved_files:
        existing = order.requirements or ""
        file_list = "\n".join(saved_files)
        order.requirements = f"{existing}\n\n[Attachments: {len(saved_files)} file(s)]\n{file_list}"
        db.session.commit()

    return order

def update_order_status(order, **kwargs):
    for k, v in kwargs.items():
        if hasattr(order, k):
            setattr(order, k, v)
    db.session.commit()
    return order


NON_PAGE_ORDER_TYPES = {
    "coding-project",
    "data-analysis",
    "software-development",
    "programming-assignment",
}

NON_PAGE_BASE_PRICE = {
    "coding-project": 100,
    "data-analysis": 100,
}

BASE_PRICES = {
    # category: base$/page
    "literature": 12,
    "english": 12,
    "art": 12,
    "psychology": 12,
    "philosophy": 12,
    "history": 12,
    "science": 15,
    "mathematics": 18,
    "business": 13,
    "technology": 18,
    "engineering": 18,
    "law": 13,
    "medicine": 14,
    "nursing": 13,
    "healthcare": 13,
    "geography": 12,
    "political-science": 12,
    "economics": 14,
    "physics": 15,
    "biology": 15,
    "environmental-science": 12,
    "finance": 14,
    "sociology": 12,
    "criminology": 12,
    "anthropology": 12,
    "astronomy": 13,
    "marketing": 12,
    "management-strategic-leadership": 12,
    "linguistics": 12,
    "cultural-studies": 12,
    "journalism": 12,
    "global-issues-disaster-management": 12,
    "cyber-security": 18,
    "computer-science": 18,
    "data-science": 18,
    "data-analysis": 18,
    "accounting": 14,
    "statistics": 14,
    "chemistry": 14,
    "behavioural-science": 12,
    "religion-theology": 12,
    "american-history": 12,
    "american-government": 12,
    "public-administration": 12,
    "education": 12,
    "anatomy": 15,
    "programming": 18,
    "website-app-development": 18,
    "other": 15,
}

ORDER_TYPE_MULTIPLIER = {
    "essay": 1.0,
    "excel": 1.3,
    "quizes": 1.1,
    "online-test-exam": 1.1,
    "article-review": 1.2,
    "annotated-bibiliography": 1.1,
    "presentation-speech": 1.1,
    "powerpoint-slides": 0.7,
    "problem-solving": 1.4,
    "term-paper": 1.2,
    "coursework": 1.2,
    "calculation": 1.4,
    "case-brief": 1.2,
    "report": 1.2,
    "creative-writing": 1.2,
    "synthesis-paper": 1.2,
    "critical-thinking": 1.2,
    "reflection": 1.2,
    "business-plan": 1.2,
    "marketing-plan": 1.2,
    "design-drawing": 1.5,
    "research-paper": 1.2,
    "thesis": 1.5,
    "dissertation": 1.5,
    "case-study": 1.2,
    "lab-report": 1.4,
    "coding-project": 1.6,
    "other": 1.7,
    "discussion-post": 1.0,
    "editing": 0.8,
    "rewriting": 0.9,
    "admission-essay": 1.0,
    "resume": 1.2,
    "cover-letter": 1.2,
}

DEADLINE_MULTIPLIER = [
    (3, 1.8),
    (6, 1.65),
    (12, 1.5),
    (24, 1.35),
    (48, 1.2),
    (72, 1.1),
    (9999, 1.0),
]

def compute_deadline_multiplier(deadline, now):
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    hours = (deadline - now).total_seconds() / 3600
    
    for max_hours, mult in DEADLINE_MULTIPLIER:
        if hours <= max_hours:
            return mult
    
    return 1.0

def calculate_minimum_price(category, order_type, pages, deadline, now):
    if order_type in NON_PAGE_BASE_PRICE:
        base = NON_PAGE_BASE_PRICE[order_type]
    else:
        base = BASE_PRICES.get(category, 5)
    
    type_mult = ORDER_TYPE_MULTIPLIER.get(order_type, 1)
    
    urgency_mult = compute_deadline_multiplier(deadline, now)

    # If this order type should NOT use pages, treat pages as 1 unit
    if order_type in NON_PAGE_ORDER_TYPES:
        effective_units = 1
    else:
        effective_units = pages if pages else 1

    return round(base * effective_units * type_mult * urgency_mult, 2)
