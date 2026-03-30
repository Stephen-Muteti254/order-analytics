import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"

    SQLALCHEMY_DATABASE_URI = (
        f"{os.getenv('DATABASE_URL')}"
        f"?sslmode=verify-full"
        f"&sslrootcert={os.getenv('DB_SSLROOTCERT')}"
    )

    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8080"
    )

    RATELIMIT_HEADERS_ENABLED = True

    FREE_ORDER_LIMIT = os.getenv("FREE_ORDER_LIMIT", 2)

    ACCESS_EXPIRES = int(os.getenv("ACCESS_EXPIRES", 86400))
    REFRESH_EXPIRES = int(os.getenv("REFRESH_EXPIRES", 86400))
    basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "/var/data/uploads")
    # UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "uploads")
    # UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", os.path.join(basedir, "uploads"))

    UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, "applications")
    ORDERS_FOLDER = os.path.join(UPLOAD_ROOT, "orders")
    SUBMISSIONS_FOLDER = os.path.join(UPLOAD_ROOT, "submissions")
    SUPPORT_UPLOADS_FOLDER = os.path.join(UPLOAD_ROOT, "support_chats")
    PROFILES_FOLDER = os.path.join(UPLOAD_ROOT, "profiles")
    CAREERS_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, "careers")
    INSIGHTPAY_SURVEY_UPLOADS_FOLDER = os.path.join(UPLOAD_ROOT, "insightpay_surveys")

    WRITER_PAYOUT_PERCENTAGE = 0.30
    PAYSTACK_SECRET_KEY = "sk_live_e3c9231206431254561a88cd7d12b50098fe21f6"
    PAYSTACK_PUBLIC_KEY = "pk_live_9b05dd85fd1beb35e3feb8571ae5c8f5abbc39f8"

    EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME")
    EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS")
    FRONTEND_URL = os.getenv("FRONTEND_URL", "https://academichubpro.com")

    ZOHO_SMTP_HOST = os.getenv("ZOHO_SMTP_HOST", "smtppro.zoho.com")
    ZOHO_SMTP_PORT = int(os.getenv("ZOHO_SMTP_PORT", 465))
    ZOHO_APP_PASSWORD = os.getenv("ZOHO_APP_PASSWORD")

    INSIGHTPAY_EMAIL_FROM_NAME = os.getenv("INSIGHTPAY_EMAIL_FROM_NAME")
    INSIGHTPAY_EMAIL_FROM_ADDRESS = os.getenv("INSIGHTPAY_EMAIL_FROM_ADDRESS")
    INSIGHTPAY_FRONTEND_URL = os.getenv("INSIGHTPAY_FRONTEND_URL", "https://insightpay.com")

    INSIGHTPAY_ZOHO_APP_PASSWORD = os.getenv("ZOHO_APP_PASSWORD")

    EMAIL_VERIFY_EXPIRES = int(os.getenv("EMAIL_VERIFY_EXPIRES", 3600))

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
