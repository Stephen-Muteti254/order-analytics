import re
from app.extensions import db
from app.models.chat import Chat
from app.models.message import Message

# ---------------------------------------
# 1. TEXT NORMALIZATION (obfuscation fixing)
# ---------------------------------------

def normalize_text(t: str):
    if not t:
        return t

    # Remove strange separators like []{}()/\ inside emails
    # t = re.sub(r"[\[\]\(\)\{\}\/\\]+", "", t)
    t = re.sub(r"(?<=\w)[\[\]\(\)\{\}](?=\w)", "", t)

    # Convert obfuscated email words -> symbols
    replacements = {
        r"\s+at\s+": "@",
        r"\s+dot\s+": ".",
        r"\(at\)": "@",
        r"\(dot\)": ".",
    }
    for pat, repl in replacements.items():
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)

    # Convert written numbers to digits (for phone obfuscation)
    words_to_nums = {
        "zero": "0", "one": "1", "two": "2", "three": "3",
        "four": "4", "five": "5", "six": "6",
        "seven": "7", "eight": "8", "nine": "9"
    }
    for word, digit in words_to_nums.items():
        t = re.sub(rf"\b{word}\b", digit, t, flags=re.IGNORECASE)

    # Remove separators between phone digits (spaces, commas, periods)
    t = re.sub(r"(?<=\d)[ ,.-]+(?=\d)", "", t)

    return t


# ---------------------------------------
# 2. REGEX PII DETECTION (catches obfuscated data)
# ---------------------------------------

PII_REGEX_PATTERNS = [
    # Full emails
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",

    # Kenyan-like phones and international phones
    r"\+?\d{9,15}",

    # Slightly broken emails (muteti@gma, muteti@gmail without .com)
    r"[A-Za-z0-9._%+-]+@[A-Za-z]+",

    # Things like: muteti@gm, muteti@gnai, etc
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+",

    # Separated digits sequences 3-3-4
    r"\b\d{3}[-\s.]?\d{3}[-\s.]?\d{3,4}\b",
]

def regex_mask(t: str):
    for pat in PII_REGEX_PATTERNS:
        t = re.sub(pat, "[REDACTED]", t, flags=re.IGNORECASE)
    return t


# ---------------------------------------
# 3. PRESIDIO (OPTIONAL) — add if installed
# ---------------------------------------

try:
    from presidio_analyzer import AnalyzerEngine
    analyzer = AnalyzerEngine()

    def presidio_mask(t: str):
        results = analyzer.analyze(text=t, language="en")
        for r in sorted(results, key=lambda x: x.start, reverse=True):
            t = t[:r.start] + "[REDACTED]" + t[r.end:]
        return t

except Exception:
    analyzer = None

    def presidio_mask(t: str):
        return t  # fallback if Presidio not available


# ---------------------------------------
# 4. MAIN SANITIZER PIPELINE (call everywhere)
# ---------------------------------------

def sanitize_message(content: str) -> str:
    """Runs normalization → presidio → regex in that order."""

    if not content:
        return content

    # STEP 1: normalize obfuscations
    clean = normalize_text(content)

    # STEP 2: presidio (if installed)
    clean = presidio_mask(clean)

    # STEP 3: regex fallback & extended matching
    clean = regex_mask(clean)

    return clean


# ---------------------------------------
# 5. Chat service API
# ---------------------------------------

def get_or_create_chat(order_id, client_id, writer_id):
    chat = Chat.query.filter_by(
        order_id=order_id, 
        client_id=client_id, 
        writer_id=writer_id
    ).first()

    if not chat:
        chat = Chat(order_id=order_id, client_id=client_id, writer_id=writer_id)
        db.session.add(chat)
        db.session.commit()

    return chat


def add_message(chat_id, sender_id, content):
    """All new messages are automatically sanitized."""
    # sanitized = sanitize_message(content)

    msg = Message(chat_id=chat_id, sender_id=sender_id, content=content)
    db.session.add(msg)
    db.session.commit()
    return msg
