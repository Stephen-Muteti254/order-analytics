from flask import Flask, jsonify
from .config import DevelopmentConfig, ProductionConfig
from .extensions import db, migrate, jwt, ma, cors, bcrypt
import os
from flask_cors import CORS
import os
from app.config import Config
import re

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

def create_app(config_name=None):
    app = Flask(
        __name__,
        instance_relative_config=False,
        template_folder=os.path.join(BASE_DIR, "templates")
    )

    env = os.getenv("FLASK_ENV", "development")
    if env == "production":
        app.config.from_object(ProductionConfig)
    else:
        app.config.from_object(DevelopmentConfig)


    UPLOAD_DIRS = [
        Config.UPLOAD_FOLDER,
        Config.ORDERS_FOLDER,
        Config.SUBMISSIONS_FOLDER,
        Config.SUPPORT_UPLOADS_FOLDER,
        Config.PROFILES_FOLDER,
        Config.CAREERS_UPLOAD_FOLDER,
        Config.INSIGHTPAY_SURVEY_UPLOADS_FOLDER,
    ]

    # for path in UPLOAD_DIRS:
    #     os.makedirs(path, exist_ok=True)

    # initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    ma.init_app(app)


    # parse env list for dev (localhost) and prod
    origins = [o.strip() for o in app.config["CORS_ORIGINS"].split(",")]

    origins.extend([
        "https://academichubpro.com",
        "https://itnest.org",
        "https://insightpay.org",
    ])

    # Subdomains (regex)
    origins.append(re.compile(r"https://.*\.academichubpro\.com"))
    origins.append(re.compile(r"https://.*\.itnest\.org"))
    origins.append(re.compile(r"https://.*\.insightpay\.org"))

    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    bcrypt.init_app(app)
    # limiter.init_app(app)

    # register blueprints
    from app.routes.auth_routes import bp as auth_bp
    from app.routes.order_routes import bp as order_bp
    from app.routes.bid_routes import bp as bid_bp
    from app.routes.notification_routes import bp as notification_bp
    from app.routes.payment_routes import bp as payment_bp
    from app.routes.admin_payments_routes import bp as admin_payment_bp
    from app.routes.chat_routes import bp as chat_bp
    from app.routes.leaderboard_routes import bp as leaderboard_bp
    from app.routes.profile_routes import bp as profile_bp
    from app.routes.application_routes import bp as application_bp
    from app.routes.admin_client_routes import bp as admin_clients_bp
    from app.routes.admin_writers import bp as admin_writers_bp
    from app.routes.user_routes import bp as user_bp
    from app.routes.submission_routes import bp as submission_bp
    from app.routes.support_chat_routes import bp as support_chat_bp
    from app.routes.career_routes import bp as career_bp
    from insightpay.routes.auth_routes import bp as insightpay_auth_bp
    from insightpay.routes.application_routes import bp as insightpay_application_bp
    from insightpay.routes.survey_routes import bp as insightpay_surveys_bp
    from insightpay.routes.survey_routes import user_surveys_bp as insightpay_user_surveys_bp
    from insightpay.routes.admin import admin_bp as insightpay_admin_bp
    from insightpay.routes.wallet_routes import bp as insightpay_wallet_bp
    from insightpay.routes.dashboard_routes import bp as insightpay_dashboard_bp
    from app.routes.assessment_routes import bp as assessment_bp

    # available orders optional
    try:
        from app.routes.available_orders_routes import bp as available_orders_bp
        app.register_blueprint(available_orders_bp)
    except Exception:
        pass

    app.register_blueprint(auth_bp)
    app.register_blueprint(order_bp)
    app.register_blueprint(bid_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(admin_payment_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(leaderboard_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(application_bp)
    app.register_blueprint(admin_clients_bp)
    app.register_blueprint(admin_writers_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(submission_bp)
    app.register_blueprint(support_chat_bp)
    app.register_blueprint(career_bp)
    app.register_blueprint(insightpay_auth_bp)
    app.register_blueprint(insightpay_application_bp)
    app.register_blueprint(insightpay_surveys_bp)
    app.register_blueprint(insightpay_user_surveys_bp)
    app.register_blueprint(insightpay_admin_bp)
    app.register_blueprint(insightpay_wallet_bp)
    app.register_blueprint(insightpay_dashboard_bp)
    app.register_blueprint(assessment_bp)

    # error handlers to match required error format
    from app.utils.response_formatter import error_response

    @app.errorhandler(400)
    def bad_request(e):
        return error_response("BAD_REQUEST", str(e), status=400)

    @app.errorhandler(401)
    def unauthorized(e):
        return error_response("UNAUTHORIZED", str(e), status=401)

    @app.errorhandler(404)
    def not_found(e):
        return error_response("NOT_FOUND", "Resource not found", status=404)

    @app.errorhandler(500)
    def server_error(e):
        return error_response("SERVER_ERROR", "Internal server error", status=500)

    return app
