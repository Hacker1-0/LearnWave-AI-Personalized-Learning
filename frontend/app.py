"""LearnWave: adaptive learning dashboard and content generator."""
from __future__ import annotations

import os
import json
import re
import hashlib
import hmac
import time
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st
from backend.learning_engine import CONTENT_TYPES, LEVELS, GenerationError, check_api_key, generate_content, recommend_level
from backend.local_ai import LocalAIError, configured_local_model, local_models

st.set_page_config(page_title="LearnWave AI", page_icon="✦", layout="wide", initial_sidebar_state="expanded")

NAV = [
    ("Home", "home"), ("My Learning Path", "route"), ("Courses", "menu_book"),
    ("AI Assistant", "auto_awesome"), ("Progress & Analytics", "monitoring"),
    ("Notes & Flashcards", "note_stack"), ("Quizzes & Practice", "quiz"),
    ("Doubt Solver", "lightbulb"), ("Study Resources", "folder_open"), ("Settings", "settings"),
]
SUBJECTS = [
    ("Operating Systems", "3/6 complete", "Process Scheduling", "blue"),
    ("Data Structures & Algorithms", "2/5 complete", "B Tree & B+ Tree", "purple"),
    ("Cloud Computing", "1/4 complete", "AWS — EC2, S3, EBS", "teal"),
    ("AI & Software Engineering", "0/6 complete", "Python OOP & Design Patterns", "orange"),
]
PLAN_ITEMS = [
    ("Operating Systems — Unit 2 (Process Scheduling)", "2:00 PM – 3:30 PM", "teal"),
    ("DSC — B Tree & B+ Tree", "4:00 PM – 5:00 PM", "blue"),
    ("Cloud Computing — AWS (EC2, S3)", "6:00 PM – 7:00 PM", "purple"),
    ("Revision + practice", "8:00 PM – 9:00 PM", "orange"),
]
MAX_UPLOAD_BYTES = 1_000_000
AI_MODES = {"Automatic (offline AI first)": "auto", "Offline AI (no internet)": "local", "Gemini (online)": "gemini"}


def _truthy(value: object, default: bool = False) -> bool:
    """Interpret a value from server-side configuration as a boolean."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _secret_group(name: str) -> object:
    try:
        return st.secrets.get(name, {})
    except Exception:
        return {}


def app_config() -> object:
    return _secret_group("app")


def auth_config() -> object:
    return _secret_group("auth")


def login_required() -> bool:
    """Require Google sign-in only when the host explicitly enables it."""
    environment_value = os.getenv("LEARNWAVE_REQUIRE_LOGIN")
    if environment_value is not None:
        return _truthy(environment_value)
    settings = app_config()
    return _truthy(settings.get("require_login", False)) if hasattr(settings, "get") else False


def google_auth_is_configured() -> bool:
    """Check only that private Google OIDC configuration exists; never expose it."""
    auth = auth_config()
    if not hasattr(auth, "get"):
        return False
    google = auth.get("google", {})
    if not hasattr(google, "get"):
        return False
    common_fields = ("redirect_uri", "cookie_secret")
    provider_fields = ("client_id", "client_secret", "server_metadata_url")
    return all(str(auth.get(field, "")).strip() for field in common_fields) and all(
        str(google.get(field, "")).strip() for field in provider_fields
    )


def user_is_logged_in() -> bool:
    try:
        return bool(st.user.get("is_logged_in", False))
    except (AttributeError, KeyError, TypeError):
        return False


def user_claim(name: str, default: str = "") -> str:
    try:
        value = st.user.get(name, default)
    except (AttributeError, KeyError, TypeError):
        value = default
    return str(value or default).strip()


def creator_emails() -> set[str]:
    """Read the creator allowlist only from host-side Secrets or environment."""
    values: object = []
    settings = app_config()
    if hasattr(settings, "get"):
        values = settings.get("creator_emails", [])
    environment_values = os.getenv("LEARNWAVE_CREATOR_EMAILS", "")
    if environment_values:
        values = [*([values] if isinstance(values, str) else values), *environment_values.split(",")]
    if isinstance(values, str):
        values = values.split(",")
    try:
        return {str(value).strip().casefold() for value in values if str(value).strip()}
    except TypeError:
        return set()


def is_creator() -> bool:
    """Creator access needs a verified Google identity and server-side allowlist match."""
    email = user_claim("email").casefold()
    verified = _truthy(user_claim("email_verified"))
    return bool(email and verified and email in creator_emails() and user_is_logged_in())


def current_user_name() -> str:
    return user_claim("name") or "Krish"


def profile_destination() -> str:
    return "Creator Console" if is_creator() else "Settings"


def render_google_login() -> None:
    st.markdown(
        """
        <div class="login-card">
          <div class="login-mark">✦</div>
          <div><h1>Welcome to LearnWave <span>AI</span></h1>
          <p>Sign in securely with Google to continue learning.</p></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.button("Continue with Google", key="login_with_google", type="primary",
              on_click=st.login, args=("google",), use_container_width=True)
    st.caption("Google handles sign-in. LearnWave never receives or stores your Google password.")


def require_authenticated_user() -> None:
    """Stop protected deployments before any dashboard, settings, or data are rendered."""
    if not login_required():
        return
    if not google_auth_is_configured():
        st.error("Secure login is required, but Google sign-in has not been configured on this server yet.")
        st.caption("Add the private Google OAuth and app settings in the host's Secrets manager, then restart the app.")
        st.stop()
    if not user_is_logged_in():
        render_google_login()
        st.stop()


def _check_demo_password(expected: str) -> None:
    entered = str(st.session_state.get("demo_password_input", ""))
    if hmac.compare_digest(entered, expected):
        st.session_state["demo_password_authenticated"] = True
        st.session_state["demo_password_error"] = False
    else:
        st.session_state["demo_password_error"] = True


def require_demo_password() -> None:
    """Gate a temporary remote demo with a server-side passcode."""
    expected = os.getenv("LEARNWAVE_DEMO_PASSWORD", "")
    if not expected or st.session_state.get("demo_password_authenticated", False):
        return

    st.markdown(
        """
        <div class="login-card">
          <div class="login-mark">✦</div>
          <div><h1>Private LearnWave <span>Demo</span></h1>
          <p>Enter the passcode shared by the project creator to continue.</p></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("learnwave_demo_password_form"):
        st.text_input("LearnWave passcode", type="password", key="demo_password_input")
        st.form_submit_button("Unlock LearnWave", type="primary", key="demo_unlock",
                              on_click=_check_demo_password, args=(expected,))
    if st.session_state.pop("demo_password_error", False):
        st.error("That passcode did not match. Try again.")
    if st.session_state.get("demo_password_authenticated", False):
        return
    st.caption("This passcode is only for LearnWave access. It is not your Google or Gemini password.")
    st.stop()


def get_key() -> str:
    try:
        secret = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        secret = ""
    session_key = st.session_state.get("runtime_gemini_key", "") if local_key_setup_allowed() else ""
    return secret or os.getenv("GEMINI_API_KEY", "") or session_key


def is_loopback_request() -> bool:
    """A convenience check for private local setup, never a public authorization mechanism."""
    try:
        host = urlsplit("//" + st.context.headers.get("host", "")).hostname
    except Exception:
        host = None
    return (host or "").lower() in {"localhost", "127.0.0.1", "::1"}


def local_key_setup_allowed() -> bool:
    """Only allow key file setup in an unauthenticated local-development session."""
    return is_loopback_request() and not login_required() and not _truthy(os.getenv("LEARNWAVE_REMOTE_DEMO"))


def save_local_key_file(api_key: str) -> None:
    """Write a local key to the git-ignored Streamlit secrets file, preserving other entries."""
    if not local_key_setup_allowed():
        raise PermissionError("Local secret-file setup is disabled outside private development.")
    secret_path = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    existing = secret_path.read_text(encoding="utf-8") if secret_path.exists() else ""
    key_line = f"GEMINI_API_KEY = {json.dumps(api_key)}"
    key_pattern = re.compile(r"(?m)^\s*GEMINI_API_KEY\s*=.*$")
    if key_pattern.search(existing):
        updated = key_pattern.sub(lambda _: key_line, existing, count=1)
    else:
        first_table = re.search(r"(?m)^\s*\[", existing)
        if first_table:
            updated = existing[:first_table.start()].rstrip() + "\n" + key_line + "\n\n" + existing[first_table.start():]
        elif existing.strip():
            updated = existing.rstrip() + "\n" + key_line + "\n"
        else:
            updated = key_line + "\n"
    temp_path = secret_path.with_name(secret_path.name + ".tmp")
    temp_path.write_text(updated, encoding="utf-8")
    temp_path.replace(secret_path)


def set_page(page: str) -> None:
    st.session_state["page"] = page
    st.session_state["home_show_result"] = False
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)


def study_topic(topic: str) -> None:
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)
    st.session_state["prefill_topic"] = topic
    st.session_state["page"] = "Create Content"


def ask_suggestion(question: str) -> None:
    st.session_state["home_show_result"] = False
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)
    st.session_state["prefill_topic"] = question
    st.session_state["page"] = "AI Assistant"


def profile() -> tuple[str, int | None, str]:
    score = int(st.session_state.get("profile_score", 65)) if st.session_state.get("profile_has_score", True) else None
    level, reason = recommend_level(st.session_state.get("profile_level", "Intermediate"), score)
    return level, score, reason


def learning_metrics() -> dict[str, int]:
    """Summarize real activity and checklist state for this Streamlit session."""
    activity = st.session_state.get("activity", [])
    topics = st.session_state.get("topics_seen", [])
    completed = sum(bool(st.session_state.get(f"plan_done_{idx}", False)) for idx in range(len(PLAN_ITEMS)))
    total = len(PLAN_ITEMS)
    return {
        "topics": len(topics),
        "lessons": int(st.session_state.get("lessons_generated", len(activity))),
        "quizzes": len(st.session_state.get("quiz_attempts", [])),
        "plan_percent": round(completed / total * 100) if total else 0,
        "plan_completed": completed,
        "plan_total": total,
    }


def weekly_activity_plot() -> tuple[str, str, str]:
    """Return a tiny SVG trace and labels from actual activity in this session."""
    daily = st.session_state.get("daily_activity", {})
    days = [date.today() - timedelta(days=offset) for offset in range(6, -1, -1)]
    values = [int(daily.get(day.isoformat(), 0)) for day in days]
    peak = max(values, default=0) or 1
    coordinates = [(4 + index * 42, 62 - round(value / peak * 48)) for index, value in enumerate(values)]
    path = "M" + " L".join(f"{x},{y}" for x, y in coordinates)
    circles = "".join(f'<circle cx="{x}" cy="{y}" r="3"/>' for x, y in coordinates)
    labels = "".join(f"<span>{day.strftime('%a')}</span>" for day in days)
    return path, circles, labels


def generate(topic: str, kind: str, level: str, score: int | None) -> None:
    st.session_state.pop("error", None)
    if not topic.strip():
        st.session_state.error = "Enter a topic or question first."
        return
    provider = AI_MODES.get(st.session_state.get("generation_mode"), "auto")
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    signature = hashlib.sha256(json.dumps([topic.strip(), kind, level, score, provider, model, configured_local_model()]).encode()).hexdigest()
    cache = st.session_state.setdefault("answer_cache", {})
    if signature in cache:
        st.session_state.result = {**cache[signature], "cached": True}
        return
    preview = st.empty()
    started = time.monotonic()
    try:
        with st.status("Preparing your personalized lesson…", expanded=False) as progress:
            result = generate_content(
                topic=topic.strip(), content_type=kind, level=level, quiz_score=score,
                api_key=get_key().strip() if provider != "local" else "", model=model,
                provider=provider,
                on_text=lambda text: preview.markdown(text + " ▌") if text else preview.empty(),
                on_status=lambda message: progress.update(label=message),
            )
            progress.update(label="Your lesson is ready", state="complete", expanded=False)
        result["content_type"] = kind
        result["topic"] = topic.strip()[:120]
        result["level"] = level
        result["elapsed"] = round(time.monotonic() - started, 1)
        cache[signature] = result.copy()
        while len(cache) > 20:
            cache.pop(next(iter(cache)))
        st.session_state.result = result
        history = st.session_state.setdefault("activity", [])
        now = datetime.now()
        history.insert(0, {
            "topic": topic.strip()[:120], "kind": kind,
            "when": now.strftime("%I:%M %p"), "date": now.date().isoformat(),
        })
        del history[8:]
        st.session_state["lessons_generated"] = int(st.session_state.get("lessons_generated", 0)) + 1
        topics = st.session_state.setdefault("topics_seen", [])
        normalized_topic = topic.strip()[:120]
        if normalized_topic.casefold() not in {item.casefold() for item in topics}:
            topics.append(normalized_topic)
        daily = st.session_state.setdefault("daily_activity", {})
        daily[now.date().isoformat()] = int(daily.get(now.date().isoformat(), 0)) + 1
    except Exception as exc:
        st.session_state.error = str(exc) if isinstance(exc, (GenerationError, LocalAIError)) else "The answer could not be completed. Please retry or choose another AI source."
    finally:
        preview.empty()


def show_result() -> None:
    failed_request = bool(st.session_state.get("error"))
    if st.session_state.get("error"):
        st.warning(st.session_state.pop("error"))
    result = st.session_state.get("result")
    if not result:
        return
    if failed_request:
        st.caption("Your previous answer is kept below; it is not an answer to the failed request.")
    if result["source"] == "template":
        st.info("Preview mode is active. Add GEMINI_API_KEY to the host’s server-side secrets to enable Gemini.")
    elif result["source"] == "local":
        st.success(f"Offline AI · {result['model']} · {result.get('level', '')}")
    else:
        st.success(f"Generated with Gemini · {result['model']}")
    if result.get("cached"):
        st.caption("Saved answer from this session — shown instantly for the same topic and learner settings.")
    elif "elapsed" in result:
        st.caption(f"Completed in {result['elapsed']} seconds")
    with st.container(border=True):
        kind = result.get("content_type")
        text = result["text"]
        if kind == "Flashcards":
            cards = re.findall(
                r"###\s*Card\s*\d+\s*\n\*\*Front:\*\*\s*(.*?)\s*\n\*\*Back:\*\*\s*(.*?)(?=\n###\s*Card\s*\d+|\Z)",
                text, flags=re.IGNORECASE | re.DOTALL,
            )
            if cards:
                st.markdown("### Click a card to reveal the answer")
                for number, (front, back) in enumerate(cards, start=1):
                    with st.expander(f"Card {number} · {front.strip()}"):
                        st.markdown(back.strip())
            else:
                st.markdown(text)
        elif kind == "Practice questions":
            match = re.search(r"(?im)^\s*(?:#{1,3}\s*)?(?:answer key|answers)\s*:?", text)
            if match:
                st.markdown(text[:match.start()])
                with st.expander("Check your answers"):
                    st.markdown(text[match.end():])
            else:
                st.markdown(text)
            st.slider("Self-assessed quiz score", 0, 100,
                      value=int(st.session_state.get("profile_score", 65)), key="quiz_score_input")
            st.button("Save score for my next lesson", key="save_quiz_score", on_click=save_quiz_score)
        else:
            st.markdown(text)


def heading(title: str, subtitle: str) -> None:
    st.markdown(f"<div class='kicker'>LEARNWAVE / {title.upper()}</div>", unsafe_allow_html=True)
    st.title(title)
    st.caption(subtitle)


def section(title: str, subtitle: str = "", action: str = "") -> None:
    if not action:
        st.markdown(f"<div class='section'><div><b>{title}</b><small>{subtitle}</small></div></div>", unsafe_allow_html=True)
        return
    destination = {
        ("Quick actions", "View all"): "Create Content",
        ("Your learning path", "Full path"): "My Learning Path",
        ("Study plan", "View calendar"): "Study Plan",
        ("Recommended for you", "View all"): "Courses",
    }.get((title, action), "Study Resources")
    title_col, action_col = st.columns([4, 1], gap="small")
    with title_col:
        st.markdown(f"<div class='section'><div><b>{title}</b><small>{subtitle}</small></div></div>", unsafe_allow_html=True)
    with action_col:
        st.button(f"{action} →", key=f"section_{title.replace(' ', '_')}", on_click=set_page, args=(destination,), use_container_width=True)


def open_resource(page: str, topic: str, content_type: str | None = None) -> None:
    st.session_state.pop("result", None)
    st.session_state.pop("error", None)
    st.session_state["page"] = page
    st.session_state["prefill_topic"] = topic
    if content_type:
        st.session_state["prefill_type"] = content_type


def css() -> None:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Caveat:wght@500;600;700&family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');
    :root{--bg:#050a16;--panel:#0a1730;--line:#164477;--muted:#92a9ca;--white:#f1f6ff}
    .st-key-mobile_header,.st-key-mobile_nav,.st-key-mobile_assistant,.st-key-mobile_showcase,.st-key-mobile_motivation,.st-key-mobile_generate_cta{display:none}
    .stApp{background:radial-gradient(ellipse at 52% -18%,#16315b 0%,#09152b 37%,#040916 78%);color:var(--white)}
    .login-card{display:flex;align-items:center;gap:18px;max-width:540px;margin:13vh auto 1.1rem;padding:28px;border:1px solid #2a64a5;border-radius:20px;background:radial-gradient(ellipse at 100% 0,#4e268066,transparent 48%),linear-gradient(145deg,#0d2146,#09152d);box-shadow:0 14px 44px #0008}
    .login-card .login-mark{display:grid;place-items:center;flex:0 0 62px;width:62px;height:62px;border:1px solid #5c9cff;border-radius:18px;background:radial-gradient(circle,#2e66c9,#241b67);box-shadow:0 0 28px #6b3cffaa;color:#d6ecff;font-size:32px}
    .login-card h1{margin:0!important;font-size:25px!important}.login-card h1 span{color:#bd58ff}.login-card p{margin:7px 0 0;color:#b5c7e4}
    html,body,[class*="css"]{font-family:'DM Sans',sans-serif}
    h1,h2,h3,h4{font-family:'Space Grotesk',sans-serif!important;color:#f1f6ff!important;letter-spacing:-.025em}
    [data-testid="stHeader"]{height:0;background:transparent}
    [data-testid="stSidebar"]{background:linear-gradient(180deg,#08152b,#050b18);border-right:1px solid #19385f;min-width:265px!important}
    [data-testid="stSidebar"]>div:first-child{padding-top:1rem}
    [data-testid="stSidebar"] .stButton button{display:flex;align-items:center;justify-content:flex-start;height:42px;padding:0 13px;text-align:left;line-height:1;border:1px solid transparent;border-radius:11px;background:transparent;color:#c6d7ef;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:all .15s}
    [data-testid="stSidebar"] .stButton button>div,[data-testid="stSidebar"] .stButton button p{width:100%!important;margin:0!important;text-align:left!important;justify-content:flex-start!important}
    [data-testid="stSidebar"] .st-key-sidebar_navigation button{gap:8px!important;padding:0 10px!important}
    [data-testid="stSidebar"] .st-key-sidebar_navigation button [data-testid="stIconMaterial"]{flex:0 0 23px;width:23px;font-size:22px;color:#78c7ff}
    [data-testid="stSidebar"] .st-key-sidebar_navigation button[kind="primary"] [data-testid="stIconMaterial"]{color:white;filter:drop-shadow(0 0 5px #be63ff)}
    [data-testid="stSidebar"] .stButton button:hover{border-color:#236dc1;background:#0b1e3b;color:white}
    [data-testid="stSidebar"] .st-key-sidebar_generate{margin:2px 0 13px}
    [data-testid="stSidebar"] .st-key-sidebar_generate button{justify-content:center!important;height:45px!important;padding:0 12px!important;border:1px solid #a34cff!important;border-radius:13px!important;background:linear-gradient(100deg,#7226d6,#168cff)!important;box-shadow:0 7px 20px #5523ba62!important;color:#fff!important;font-size:12px!important;font-weight:700!important}
    [data-testid="stSidebar"] .st-key-sidebar_generate button>div,[data-testid="stSidebar"] .st-key-sidebar_generate button p{text-align:center!important}
    .sidebar-nav-heading{margin:2px 6px 7px;color:#668dc1;font-size:9px;font-weight:700;letter-spacing:.16em}
    @keyframes active-purple-pulse{0%,100%{opacity:.42}50%{opacity:1}}
    [data-testid="stSidebar"] button[kind="primary"]{position:relative;isolation:isolate;background:linear-gradient(100deg,#153e91,#36249e)!important;border:1px solid #a24fff!important;color:#fff!important}
    [data-testid="stSidebar"] button[kind="primary"]::after{content:"";position:absolute;inset:-1px;z-index:0;border:1px solid #c56aff;border-radius:inherit;box-shadow:0 0 19px #b341ffb3,inset 0 0 10px #8d35ff55;opacity:.42;pointer-events:none;animation:active-purple-pulse 2.2s ease-in-out infinite;will-change:opacity}
    [data-testid="stSidebar"] button[kind="primary"]>*{position:relative;z-index:1}
    [data-testid="stMainBlockContainer"]{max-width:2800px;width:100%;padding-top:1.3rem;padding-bottom:2.5rem}
    [data-testid="stVerticalBlockBorderWrapper"]{background:linear-gradient(145deg,rgba(11,25,49,.94),rgba(5,13,28,.97));border:1px solid #143e70;border-radius:15px;box-shadow:0 8px 26px #0003;padding:1rem}
    .stButton button{border-radius:10px;border:1px solid #1b4b7a;background:linear-gradient(110deg,#0c2145,#102a52);color:#eaf3ff;font-weight:600;transition:all .15s}
    .stButton button:hover{border-color:#28aaff;color:#fff;box-shadow:0 0 13px #087dff55}
    .stButton button[kind="primary"]{background:linear-gradient(100deg,#a329fa,#168aff)!important;border:0!important;color:white!important;box-shadow:0 5px 17px #6929dd55}
    .stTextInput input,.stTextArea textarea,.stSelectbox div[data-baseweb="select"]>div{background:#091a36!important;border-color:#204b7b!important;color:#edf4ff!important;border-radius:10px!important}
    [data-testid="stSidebar"] [data-testid="stSelectbox"] label,[data-testid="stSidebar"] [data-testid="stCheckbox"] label{color:#9bb1d1;font-size:.78rem}
    [data-testid="stCaptionContainer"]{color:#91a9ca}
    .brand{display:flex;align-items:center;gap:11px;padding:4px 5px 14px}
    .brand-icon{display:grid;place-items:center;width:48px;height:48px;border:1px solid #377bff;border-radius:15px;color:#52e4ff;font-size:30px;background:radial-gradient(circle,#1a4483,#111532 70%);box-shadow:0 0 20px #1a68ff75}
    .brand b{display:block;font:700 20px 'Space Grotesk';color:white;white-space:nowrap}.brand i{color:#bb57ff;font-style:normal}
    .brand small{display:block;margin-top:3px;font-size:9px;letter-spacing:.15em;color:#98accb}
    .rule{height:1px;margin:0 3px 12px;background:linear-gradient(90deg,#2683ed,#172644,transparent)}
    .motto{padding:16px;margin:10px 0 16px;border:1px solid #51358e;border-radius:14px;background:radial-gradient(ellipse at 90% 15%,#552987,#17193d 55%,#0b1930);min-height:105px}
    .motto b{display:block;margin:5px 0;color:white;font:600 19px 'Space Grotesk'}.motto small{color:#c1b8e5;font-size:10px}
    .profilelabel{color:#6687b5;letter-spacing:.14em;font-size:10px;font-weight:700;padding:5px 0}
    .side-art{position:relative;overflow:hidden;height:124px;margin:15px 0 10px;padding:13px;border:1px solid #503996;border-radius:13px;background:radial-gradient(ellipse at 17% 83%,#ff9ca7 0%,#a140cf 32%,transparent 64%),linear-gradient(145deg,#121b48,#391c63 62%,#111735)}
    .side-art:after{content:'';position:absolute;inset:0;background:linear-gradient(160deg,transparent 30%,#0a1630 80%);clip-path:polygon(0 73%,25% 50%,44% 68%,62% 28%,100% 73%,100% 100%,0 100%)}
    .side-art span{position:relative;z-index:2;color:#fff;font:italic 17px 'Space Grotesk';line-height:1.05;text-shadow:0 2px 7px #160b30}
    .side-art span b{color:#ffe58c;font-size:13px}.side-art i{position:absolute;z-index:2;right:15%;bottom:14px;width:25px;height:38px;border-radius:45% 45% 10% 10%;background:linear-gradient(#101326 25%,#15152b 26%);box-shadow:0 0 14px #0b102c}
    .side-art em{position:absolute;right:0;bottom:-20px;width:80%;height:54%;border-radius:50% 0 0 0;background:linear-gradient(135deg,#131b48,#070f24)}
    .side-art strong{position:absolute;z-index:2;left:41%;top:12px;color:#ffdc7b;font-size:18px}
    .quote-card{padding:13px 14px;margin:9px 0;border:1px solid #174175;border-radius:13px;background:linear-gradient(150deg,#0d1e3b,#071225)}
    .quote-card small{color:#62baff;font-size:8px;font-weight:700;letter-spacing:.1em}
    .quote-card p{margin:13px 0 7px;color:#e5efff;font:500 14px 'Space Grotesk';line-height:1.4}
    .quote-card>i{display:block;text-align:right;color:#a9bde0;font-size:10px}
    .side-mission{padding:12px 4px;border-top:1px solid #183557}.side-mission b{display:block;color:#e4edff;font-size:10px}.side-mission small{display:block;margin-top:4px;color:#8298bb;font-size:8px}
    .top-actions{height:42px;display:flex;align-items:center;justify-content:flex-end;gap:16px;color:#e5efff;font-size:17px}
    .top-actions .notify{position:relative}.top-actions .notify i{position:absolute;right:-7px;top:-7px;display:grid;place-items:center;width:13px;height:13px;border-radius:50%;background:#ef3e59;color:white;font:700 8px 'DM Sans'}
    .top-actions b{display:flex;align-items:center;gap:7px;font-size:10px;line-height:1.25}.top-actions b em{display:grid;place-items:center;width:31px;height:31px;border:1px solid #7943dc;border-radius:50%;background:#1a2750;font-style:normal}
    .top-actions small{color:#92a9ca;font-size:8px;font-weight:400}
    .hero{min-height:315px;padding-bottom:77px}
    .hero-copy{margin-top:15px}
    .hero-top small{display:inline-block;margin:5px 0 0 19px;color:#c0d1ef;font-size:10px;font-weight:400;letter-spacing:0}
    .hero-stats{position:absolute;z-index:3;left:15px;right:15px;bottom:10px;display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #20518a;border-radius:13px;background:#07142be8;backdrop-filter:blur(10px)}
    .hero-stats>div{position:relative;min-height:48px;padding:8px 8px 6px 40px;border-right:1px solid #24416b}
    .hero-stats>div:last-child{border:0}
    .hero-stats i{position:absolute;left:10px;top:10px;color:#54d4ff;font-style:normal;font-size:18px}
    .hero-stats b{display:block;color:#f5f7ff;font:700 14px 'Space Grotesk'}
    .hero-stats small{display:block;color:#9fb3d3;font-size:8px;white-space:nowrap}
    .assistant-head{position:relative;min-height:86px;padding-right:91px}
    .robot-scene{position:absolute;right:0;top:0;width:91px;height:91px;border-radius:50%;background:radial-gradient(circle,#163f80aa,#07152b00 70%)}
    .robot-face{position:absolute;top:19px;right:17px;width:59px;height:47px;border:2px solid #57baff;border-radius:17px 17px 14px 14px;background:linear-gradient(145deg,#f5fbff,#91c9ff 50%,#3157a3);box-shadow:0 0 17px #42aaff70}
    .robot-face:after{content:'';position:absolute;left:9px;right:9px;top:18px;height:13px;border-radius:10px;background:#06142b}
    .robot-face i,.robot-face b{position:absolute;z-index:1;top:21px;width:6px;height:6px;border-radius:50%;background:#1deaff;box-shadow:0 0 7px #20ffff}
    .robot-face i{left:18px}.robot-face b{right:18px}
    .robot-ear{position:absolute;right:10px;top:29px;width:9px;height:24px;border:2px solid #56bcff;border-radius:8px;background:#7f66f5}
    .robot-antenna{position:absolute;right:44px;top:8px;width:3px;height:14px;background:#49caff}
    .robot-antenna:after{content:'';position:absolute;left:-4px;top:-5px;width:10px;height:10px;border-radius:50%;background:#ca4eff;box-shadow:0 0 9px #b444ff}
    .robot-body{position:absolute;right:22px;bottom:3px;width:49px;height:21px;border:1px solid #4a9dff;border-radius:13px 13px 6px 6px;background:linear-gradient(120deg,#d7f3ff,#4779dc);color:#25376c;text-align:center;font-size:11px}
    .progress-summary{display:flex;align-items:center;gap:10px}
    .donut{flex:0 0 84px;width:84px;height:84px;display:grid;place-items:center;border-radius:50%;background:conic-gradient(#1de0e2 0 30%,#734cff 30% 68%,#132b4b 68% 100%);position:relative}
    .donut:before{content:'';position:absolute;inset:8px;border-radius:50%;background:#09162d}
    .donut>b{position:relative;z-index:1;color:#f1f6ff;font:700 17px 'Space Grotesk';text-align:center}
    .donut small{display:block;color:#95a9cb;font:400 8px 'DM Sans'}
    .legend{flex:1;min-width:0}.legend span{display:block;position:relative;padding-left:11px;margin:5px 0;color:#c0cee4;font-size:7px;white-space:nowrap}
    .legend b{float:right;color:#eef4ff;font-size:7px}
    .legend i{position:absolute;left:0;top:3px;width:6px;height:6px;border-radius:50%}
    .blue-dot{background:#42aaff}.purple-dot{background:#b850ff}.teal-dot{background:#12d7d4}.orange-dot{background:#ff7b36}
    .chart{width:100%;height:61px;margin-top:8px;border-bottom:1px solid #183153}
    .weekdays{display:flex;justify-content:space-between;color:#8ba3c6;font-size:7px}
    .resource-row{display:flex;align-items:center;gap:7px;padding:9px 0;border-bottom:1px solid #172d49}
    .resource-row:last-of-type{border:0}.resource-row>i{display:grid;place-items:center;flex:0 0 27px;height:27px;border-radius:8px;background:#302052;color:#dc83ff;font-style:normal}
    .resource-row b{display:block;color:#e3edff;font-size:8px}.resource-row small{display:block;color:#8da6c8;font-size:7px;margin-top:3px}.resource-row em{margin-left:auto;color:#72aeff;font-style:normal}
    .home-footer{display:flex;align-items:center;gap:10px;margin-top:8px;padding:12px 16px;border:1px solid #29437c;border-radius:13px;background:linear-gradient(100deg,#10163b,#18214b 48%,#321b52);color:#fff}
    .home-footer>span{color:#b7abff;font-size:22px}.home-footer b{font:600 12px 'Space Grotesk'}.home-footer small{color:#a7b7da;font-size:8px}
    .home-footer i{margin-left:auto;color:#e3caff;font:italic 10px 'Space Grotesk'}
    @media(max-width:700px){.hero-art{width:80%;opacity:.55}.hero-stats{left:7px;right:7px}.hero-stats>div{padding-left:7px;text-align:center}.hero-stats i{display:none}.hero-stats small{font-size:6px;white-space:normal}.hero-stats b{font-size:11px}.home-footer{flex-wrap:wrap}.home-footer i{width:100%;margin-left:0}}

    .hero{position:relative;overflow:hidden;min-height:275px;padding:24px 26px;border:1px solid #2166bc;border-radius:18px;background:radial-gradient(ellipse at 79% 24%,#743b9366,transparent 30%),linear-gradient(110deg,#0d1a3b 0%,#171842 48%,#302052 75%,#0b1730 100%);box-shadow:inset 0 0 60px #07142d,0 10px 30px #0005}
    .hero:after{content:'';position:absolute;inset:0;background:radial-gradient(ellipse at 76% 22%,#fca3df66,transparent 16%),radial-gradient(ellipse at 81% 48%,#fa859d50,transparent 30%),linear-gradient(180deg,transparent 55%,#060d1c 100%)}
    .hero-top,.hero-copy{position:relative;z-index:3}.hero-art{position:absolute;z-index:1;right:0;bottom:0;width:67%;height:100%;opacity:.93}.hero:after{z-index:2}.hero-top{font-size:11px;font-weight:700;letter-spacing:.1em;color:#d6e5ff}
    .hero-quote{position:absolute;z-index:4;right:22px;top:43%;color:#fff;font:600 clamp(22px,2.1vw,30px)/.88 'Caveat',cursive;text-align:right;text-shadow:0 2px 10px #10102c;transform:rotate(-7deg);pointer-events:none}
    .hero-copy{max-width:570px;margin-top:23px}.hero-tag{color:#68d4ff;font-size:9px;font-weight:700;letter-spacing:.16em}
    .hero h1{font-size:clamp(29px,3vw,43px);line-height:1.08;margin:.45rem 0;color:white!important}
    .hero h1 span{background:linear-gradient(90deg,#ce42ff,#4cb7ff);-webkit-background-clip:text;color:transparent}
    .hero p{max-width:440px;color:#cad9f4;font-size:13px}
    .mountain{position:absolute;right:0;bottom:0;width:44%;height:64%;clip-path:polygon(0 100%,18% 52%,29% 74%,49% 24%,64% 67%,80% 35%,100% 100%);background:linear-gradient(180deg,#f5a2ce,#8b50d3 43%,#18366f 76%,#08142b);opacity:.78}
    .mountain:before{content:'';position:absolute;right:25%;top:5%;width:34px;height:34px;border-radius:50%;background:#ffe5c0;box-shadow:0 0 45px 18px #f587cf88}
    .stat{min-height:77px;margin:9px 0 20px;padding:12px;border:1px solid #173e70;border-radius:13px;background:linear-gradient(140deg,#0e203e,#071226)}
    .stat span{float:left;display:grid;place-items:center;width:32px;height:32px;margin-right:9px;border-radius:10px;background:#142d5c;color:#69c4ff;font-size:18px}
    .stat strong{display:block;color:#f2f6ff;font:700 18px 'Space Grotesk'}.stat small{display:block;padding-top:3px;color:#91a8c9;font-size:9px}
    .stat.orange span{color:#ffc05c;background:#39291e}.stat.teal span{color:#44e1cc;background:#10383b}.stat.purple span{color:#d88aff;background:#321953}
    .section{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:3px 0 12px}
    .section b{display:block;color:#eaf2ff;font:600 15px 'Space Grotesk'}.section small{display:block;color:#829bbd;font-size:9px;margin-top:2px}.section>span{color:#60adff;font-size:9px}
    .icon{display:grid;place-items:center;width:34px;height:34px;margin-bottom:10px;border-radius:10px;background:#322053;color:#e28cff;font-size:19px}
    .icon.blue{background:#142e59;color:#66c1ff}.icon.teal{background:#103a3d;color:#4ce3d4}.icon.orange{background:#45291b;color:#ffb75e}
    .actiontitle{min-height:31px;color:#f0f5ff;font-size:11px;font-weight:700}.muted{display:block;color:#9aafd0;font-size:9px;line-height:1.45}
    .pathhead{display:flex;align-items:center;gap:8px;min-height:38px;margin-bottom:7px}.pathhead span{display:grid;place-items:center;min-width:32px;height:32px;border-radius:10px;background:#142e59;color:#65c1ff;font-size:17px}.pathhead b{color:#edf3ff;font-size:10px}
    .pathhead.purple span{background:#321c52;color:#dc8aff}.pathhead.teal span{background:#103a3d;color:#40e0ce}.pathhead.orange span{background:#43291d;color:#ffb45b}
    .meter{height:5px;margin:9px 0;border-radius:9px;background:#172947;overflow:hidden}.meter i{display:block;height:100%;background:linear-gradient(90deg,#16c9df,#8051ff);border-radius:9px}
    .activity{display:flex;gap:9px;align-items:center;padding:9px 0;border-bottom:1px solid #162b47}.activity:last-child{border:0}
    .activity em{display:grid;place-items:center;width:27px;height:27px;border-radius:8px;background:#302052;color:#dd86ff;font-style:normal}
    .activity b{display:block;color:#deebff;font-size:10px}.activity small{display:block;color:#849dbd;font-size:9px;margin-top:3px}
    .railtitle{font:600 15px 'Space Grotesk';color:#eff5ff;margin-bottom:5px}.beta{font:600 8px 'DM Sans';color:#e0c0ff;background:#39215d;border:1px solid #7543b6;border-radius:12px;padding:3px 7px}
    .suggest{margin:8px 0;padding:8px 10px;border:1px solid #193f6d;border-radius:9px;background:#0a1d3a;color:#c2d2e9;font-size:9px}
    .planrow,.recrow{display:flex;align-items:center;gap:9px;padding:9px 0;border-bottom:1px solid #172d49}.planrow:last-child,.recrow:last-child{border:0}
    .planrow>span,.recrow>span{display:grid;place-items:center;width:29px;height:29px;border-radius:9px;background:#142c54;color:#69c5ff}
    .planrow b,.recrow b{display:block;color:#dfebff;font-size:9px}.planrow small,.recrow small{display:block;color:#8da6c8;font-size:8px;margin-top:3px}
    .recrow>span{font-size:18px}.kicker{color:#59baff;letter-spacing:.17em;font-size:9px;font-weight:700}
    .level{display:inline-block;margin:0 0 13px;padding:7px 11px;border:1px solid #2865a4;border-radius:20px;background:#0b2344;color:#c5deff;font-size:10px}
    .level b{color:white}.level.Beginner{border-color:#267d6c;background:#0b302e}.level.Advanced{border-color:#7041a5;background:#2c1948}
    @media(max-width:1050px){.mountain{opacity:.35;width:52%}.stat{padding:9px 7px}}
    @media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
    .st-key-continue_learning_bar{margin:12px 0 22px}
    .st-key-continue_learning_bar [data-testid="stVerticalBlockBorderWrapper"]{padding:.7rem .85rem!important;border-color:#20548f!important;background:linear-gradient(105deg,#0b1b39,#0c2347)!important}
    .continue-copy b{display:block;color:#eaf3ff;font:600 12px 'Space Grotesk'}.continue-copy span{display:block;margin-top:3px;color:#94add1;font-size:9px}
    .st-key-hero_continue{margin:0!important;max-width:none!important}
    .st-key-hero_continue button{height:38px!important;font-size:11px!important;border-radius:10px!important;white-space:nowrap}
    [data-testid="stFormSubmitButton"] button{background:linear-gradient(100deg,#a329fa,#168aff)!important;border:0!important;color:#fff!important;border-radius:10px!important;box-shadow:0 5px 17px #6929dd55!important}
    [data-testid="stFormSubmitButton"] button:hover{filter:brightness(1.12);box-shadow:0 0 17px #238bff70!important}
    [data-testid="stTabs"] button{color:#c6d8f1!important;border-radius:9px!important}
    [data-testid="stTabs"] button[aria-selected="true"]{background:linear-gradient(100deg,#6338d8,#314fbd)!important;color:#fff!important}
    [data-testid="stFileUploader"] section{border:1px dashed #2861a0!important;border-radius:10px!important;background:#0a1a33!important}
    .resource-row{min-height:43px}
    @media(min-width:2200px){[data-testid="stMainBlockContainer"]{max-width:calc(100vw - 340px)}.hero{min-height:430px}.hero-stats>div{min-height:68px;padding-top:13px}.hero-copy h1{font-size:58px}.section b{font-size:19px}}
    @media(max-width:700px){.hero-art{width:80%;opacity:.55}.hero-stats{left:7px;right:7px}.hero-stats>div{padding-left:7px;text-align:center}.hero-stats i{display:none}.hero-stats small{font-size:6px;white-space:normal}.hero-stats b{font-size:11px}.home-footer{flex-wrap:wrap}.home-footer i{width:100%;margin-left:0}}
    @media(max-width:700px){
      [data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"]{display:none!important}
      [data-testid="stMainBlockContainer"]{max-width:none!important;padding:.55rem .7rem 6rem!important}
      /* Keep the Home dashboard in one phone-width column. The desktop rail
         otherwise remains beside the hero and squeezes every card. */
      [data-testid="stHorizontalBlock"]:has(.st-key-continue_learning_bar){display:flex!important;flex-direction:column!important;align-items:stretch!important;gap:.75rem!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-continue_learning_bar)>[data-testid="stColumn"]{width:100%!important;min-width:0!important;max-width:none!important;flex:0 0 auto!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-continue_learning_bar)>[data-testid="stColumn"]:nth-child(2){display:none!important}
      .st-key-mobile_generate_cta{display:block!important;margin:.2rem 0 .55rem!important}
      .st-key-mobile_generate_cta button{min-height:48px!important;font-size:13px!important}
      .st-key-mobile_header{display:block!important;margin-bottom:.45rem}
      .st-key-desktop_topbar{display:none!important}
      .st-key-mobile_nav{display:block!important;position:fixed;z-index:999;left:0;right:0;bottom:0;padding:.35rem .45rem calc(.35rem + env(safe-area-inset-bottom));background:#061126f5;border-top:1px solid #22518a;box-shadow:0 -8px 24px #020714c9;backdrop-filter:blur(14px)}
      .st-key-mobile_nav [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:repeat(5,minmax(0,1fr))!important;gap:3px!important;align-items:stretch!important}
      .st-key-mobile_nav [data-testid="stColumn"]{min-width:0!important;width:auto!important;max-width:none!important;flex:none!important}
      .st-key-mobile_nav button{display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;width:100%!important;height:54px!important;padding:3px 2px!important;border:1px solid transparent!important;background:transparent!important;font-size:9px!important;line-height:1.05!important;white-space:pre-line!important;text-align:center!important}
      .st-key-mobile_nav button>div,.st-key-mobile_nav button>div>span{display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;max-width:100%!important}
      .st-key-mobile_nav button .stMarkdownEllipsis,.st-key-mobile_nav button [data-testid="stMarkdownContainer"],.st-key-mobile_nav button p{display:block!important;max-width:100%!important;margin:0!important;overflow:visible!important;text-overflow:clip!important;white-space:pre-line!important;line-height:1.05!important}
      .st-key-mobile_nav button[kind="primary"]{background:linear-gradient(100deg,#442197,#162c72)!important;border-color:#9848ef!important;box-shadow:0 0 13px #8e36ff65!important}
      .st-key-desktop_home_assistant{display:none!important}
      .st-key-desktop_home_recommendations{display:none!important}
      .st-key-mobile_assistant{display:block!important}
      .st-key-mobile_showcase{display:block!important;margin:.4rem 0 1rem}
      .st-key-mobile_motivation{display:block!important;margin:1rem 0}
      .mobile-quote{margin:1rem 0 .35rem;color:#f1eaff;font:600 17px/1.4 'Space Grotesk';max-width:24rem}
      .mobile-attribution{text-align:right;color:#69b9ff;font:italic 14px 'Caveat',cursive}
      .st-key-mobile_motivation [data-testid="stVerticalBlockBorderWrapper"]{padding:1rem!important;background:radial-gradient(ellipse at 90% 18%,#233765,#081429 62%)!important}
      .st-key-mobile_assistant [data-testid="stVerticalBlockBorderWrapper"]{padding:.8rem!important}
      .st-key-mobile_assistant .assistant-head{min-height:67px;padding-right:72px}
      .st-key-mobile_assistant .robot-scene{transform:scale(.78);transform-origin:top right}
      .st-key-mobile_assistant .stButton button{font-size:10px!important}
      .st-key-mobile_header [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:minmax(0,1fr) 38px 38px 88px!important;gap:5px!important;align-items:center!important}
      .st-key-mobile_header [data-testid="stColumn"]{width:auto!important;min-width:0!important;flex:none!important}
      .st-key-mobile_header .brand{padding:2px 0;gap:8px}
      .st-key-mobile_header .brand-icon{width:38px;height:38px;border-radius:12px;font-size:23px}
      .st-key-mobile_header .brand b{font-size:16px}
      .st-key-mobile_header .brand small{font-size:7px;margin-top:1px}
      .st-key-mobile_header button{height:37px!important;padding:0 3px!important;border-radius:20px!important;font-size:10px!important}
      .st-key-mobile_header .stTextInput{margin-top:3px}
      .st-key-mobile_header .stTextInput input{height:39px!important;font-size:12px!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-quick_Generate_New_Content){display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:.55rem!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-quick_Generate_New_Content)>[data-testid="stColumn"]{width:auto!important;min-width:0!important;max-width:none!important;flex:none!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-quick_Generate_New_Content) [data-testid="stVerticalBlockBorderWrapper"]{height:100%;min-height:133px;padding:.75rem!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-continue_0){display:flex!important;gap:.6rem!important;overflow-x:auto!important;overscroll-behavior-x:contain;scroll-snap-type:x mandatory;padding-bottom:.35rem}
      [data-testid="stHorizontalBlock"]:has(.st-key-continue_0)>[data-testid="stColumn"]{width:190px!important;min-width:190px!important;max-width:190px!important;flex:0 0 190px!important;scroll-snap-align:start}
      [data-testid="stHorizontalBlock"]:has(.st-key-continue_0) [data-testid="stVerticalBlockBorderWrapper"]{min-height:148px;padding:.75rem!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-home_activity){display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:.55rem!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-home_activity)>[data-testid="stColumn"]{width:auto!important;min-width:0!important;max-width:none!important;flex:none!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-home_activity)>[data-testid="stColumn"]:has(.st-key-home_resources){grid-column:1/-1!important}
      .st-key-mobile_showcase [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:.55rem!important}
      .st-key-mobile_showcase [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{width:auto!important;min-width:0!important;max-width:none!important;flex:none!important}
      .st-key-mobile_showcase [data-testid="stVerticalBlockBorderWrapper"]{height:100%;padding:.8rem!important}
      .st-key-mobile_recommendations .recrow{gap:5px;padding:6px 0}
      .st-key-mobile_recommendations .recrow>span{flex:0 0 24px;width:24px;height:24px;font-size:14px}
      .st-key-mobile_recommendations .recrow b{font-size:8px}
      .st-key-mobile_recommendations .recrow small{font-size:7px}
      .st-key-mobile_recommendations .stButton button{height:27px!important;padding:2px!important;font-size:8px!important}
      .st-key-mobile_recommendations [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:minmax(0,1fr) 50px!important;gap:5px!important;align-items:center!important}
      .st-key-mobile_recommendations [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{width:auto!important;min-width:0!important;max-width:none!important;flex:none!important}
      .st-key-continue_learning_bar [data-testid="stHorizontalBlock"]{display:grid!important;grid-template-columns:minmax(0,1fr) 145px!important;gap:.55rem!important;align-items:center!important}
      .st-key-continue_learning_bar [data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{width:auto!important;min-width:0!important;max-width:none!important;flex:none!important}
      .st-key-hero_continue{max-width:none!important;margin:0!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-global_search){display:grid!important;grid-template-columns:minmax(0,1fr) 42px 42px 82px!important;gap:6px!important;align-items:center!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-global_search)>[data-testid="stColumn"]{width:auto!important;min-width:0!important;flex:none!important}
      .st-key-open_profile button{padding:0 4px!important;font-size:10px!important}
      .hero{min-height:340px;padding:17px 15px 77px;border-radius:15px}
      .hero-top{font-size:10px}
      .hero-top small{margin-left:17px;font-size:9px}
      .hero-quote{right:10px;top:auto;bottom:89px;font-size:20px}
      .hero-copy{max-width:100%;margin-top:16px}
      .hero-tag{font-size:8px}
      .hero-copy h1{font-size:clamp(25px,6.5vw,34px);max-width:100%}
      .hero-copy p{max-width:70%;font-size:11px;line-height:1.4}
      .hero-art{width:100%;opacity:.58}
      .hero-stats{left:7px;right:7px;bottom:7px;grid-template-columns:repeat(4,minmax(0,1fr));border-radius:11px}
      .hero-stats>div{min-height:53px;padding:7px 3px 5px 24px;text-align:left;border-right:1px solid #24416b!important;border-bottom:0!important}
      .hero-stats>div:last-child{border-right:0!important}
      .hero-stats i{display:block;left:5px;top:9px;font-size:13px}
      .hero-stats small{font-size:clamp(6px,1.45vw,8px);line-height:1.15;white-space:normal}
      .hero-stats b{font-size:11px}
      .section b{font-size:14px}.section small{font-size:10px}.muted{font-size:10px}
    }
    @media(max-width:420px){
      .st-key-mobile_showcase [data-testid="stHorizontalBlock"]{grid-template-columns:1fr!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-home_activity){grid-template-columns:1fr!important}
      [data-testid="stHorizontalBlock"]:has(.st-key-home_activity)>[data-testid="stColumn"]:has(.st-key-home_resources){grid-column:auto!important}
      .st-key-mobile_header [data-testid="stHorizontalBlock"]{grid-template-columns:minmax(0,1fr) 34px 34px 78px!important;gap:3px!important}
      .st-key-mobile_header .brand b{font-size:14px}
      .hero{min-height:358px}
      .hero-copy p{max-width:78%}
      .st-key-mobile_nav button{font-size:8px!important}
      .st-key-continue_learning_bar [data-testid="stHorizontalBlock"]{grid-template-columns:1fr!important}
      .st-key-mobile_header .brand-icon{width:30px;height:30px;flex:0 0 30px;font-size:19px}
      .st-key-mobile_header .brand{gap:5px}
      .st-key-mobile_header .brand b{font-size:12px}
      .st-key-mobile_header .brand small{font-size:6px}
    }
    [data-testid="stMarkdownContainer"]{overflow-wrap:break-word}
    [data-testid="stMarkdownContainer"] pre{max-width:100%;overflow-x:auto;white-space:pre}
    @media(max-width:700px){
      [data-testid="stMainBlockContainer"]{min-width:0}
      .stTextInput input,.stTextArea textarea{font-size:16px!important}
      .st-key-mobile_nav{backdrop-filter:none}
      .hero-stats{backdrop-filter:none}
    }
    @media(prefers-reduced-motion:reduce){
      [data-testid="stSidebar"] button[kind="primary"]::after{animation:none!important;will-change:auto}
      .stButton button{transition:none!important}
    }
    </style>""", unsafe_allow_html=True)
    if st.session_state.get("light_mode", False):
        st.markdown("""
        <style>
        .stApp{background:linear-gradient(145deg,#f1f5ff,#e5edfc)!important;color:#172641!important}
        h1,h2,h3,h4{color:#162846!important}
        [data-testid="stVerticalBlockBorderWrapper"]{background:#ffffff!important;border-color:#c5d7f0!important;box-shadow:0 6px 24px #193c6717!important}
        [data-testid="stSidebar"]{background:linear-gradient(180deg,#eaf1ff,#dce8fa)!important}
        [data-testid="stSidebar"] .stButton button:not([kind="primary"]){color:#233d63!important}
        .brand b{color:#172c4d!important}.brand small{color:#536987!important}
        .side-mission b{color:#172c4d!important}.side-mission small{color:#536987!important}
        .section b,.actiontitle,.pathhead b,.activity b,.planrow b,.recrow b,.resource-row b{color:#172c4d!important}
        .muted,.section small,.activity small,.planrow small,.recrow small,.resource-row small{color:#536987!important}
        .hero{box-shadow:0 12px 30px #193c6722!important}
        .stTextInput input,.stTextArea textarea,.stSelectbox div[data-baseweb="select"]>div{background:#fff!important;color:#172641!important}
        </style>""", unsafe_allow_html=True)


def sidebar() -> None:
    with st.sidebar:
        st.markdown("<div class='brand'><span class='brand-icon'>✧</span><div><b>LearnWave <i>AI</i></b><small>THINK · LEARN · GROW</small></div></div><div class='rule'></div>", unsafe_allow_html=True)
        current = st.session_state.get("page", "Home")
        st.button("✦  Generate New Content", key="sidebar_generate", type="primary",
                  use_container_width=True, on_click=set_page, args=("Create Content",))
        st.markdown("<div class='sidebar-nav-heading'>EXPLORE LEARNWAVE</div>", unsafe_allow_html=True)
        navigation = [*NAV, *([("Creator Console", "admin_panel_settings")] if is_creator() else [])]
        with st.container(key="sidebar_navigation"):
            for label, icon in navigation:
                st.button(label, icon=f":material/{icon}:", key=f"nav_{label}",
                          type="primary" if current == label else "secondary",
                          use_container_width=True, on_click=set_page, args=(label,))
        st.markdown("""
        <div class='side-art'><span>Small<br>Steps<br><b>Big Results</b></span><i></i><em></em><strong>♛</strong></div>
        <div class='quote-card'><small>✧ &nbsp; TODAY'S MOTIVATION</small><p>“Discipline today builds the freedom of tomorrow.”</p><i>— Krish</i><div class='meter'><i style='width:55%'></i></div></div>
        <div class='side-mission'><b>✧ &nbsp; Your goals. Our mission.</b><small>Learn smarter · Build skills · Create your future</small></div>
        """, unsafe_allow_html=True)


def mobile_topbar() -> None:
    """Compact app header shown on phones; desktop keeps its search-first top bar."""
    with st.container(key="mobile_header"):
        brand_col, bell_col, theme_col, profile_col = st.columns([3.5, .48, .48, 1.15], gap="small")
        with brand_col:
            st.markdown("<div class='brand'><span class='brand-icon'>✧</span><div><b>LearnWave <i>AI</i></b><small>THINK · LEARN · GROW</small></div></div>", unsafe_allow_html=True)
        with bell_col:
            pending_count = sum(not st.session_state.get(f"plan_done_{idx}", False) for idx in range(len(PLAN_ITEMS)))
            st.button(f"🔔 {pending_count}", key="mobile_notifications", help=f"{pending_count} study-plan reminders", on_click=show_notifications, use_container_width=True)
        with theme_col:
            st.button("☼" if st.session_state.get("light_mode", False) else "☾", key="mobile_theme_toggle",
                      help="Toggle light or dark appearance", on_click=toggle_theme, use_container_width=True)
        with profile_col:
            profile_text = f"👤 {current_user_name().split()[0]}{' ✦' if is_creator() else ''}"
            st.button(profile_text, key="mobile_open_profile", help="Open your account",
                      on_click=set_page, args=(profile_destination(),), use_container_width=True)
        st.text_input("Search topics", placeholder="⌕  Search any topic, concept, or question…",
                      label_visibility="collapsed", key="mobile_global_search", on_change=mobile_search_submit)


def mobile_search_submit() -> None:
    query = st.session_state.get("mobile_global_search", "").strip()
    if query:
        st.session_state.pop("result", None)
        st.session_state.pop("error", None)
        st.session_state.prefill_topic = query
        st.session_state.page = "Create Content"


def mobile_navigation() -> None:
    """Phone-sized bottom navigation for the most-used LearnWave screens."""
    current = st.session_state.get("page", "Home")
    profile_page = profile_destination()
    destinations = [("⌂\nHome", "Home"), ("◈\nPath", "My Learning Path"),
                    ("▤\nCourses", "Courses"), ("✦\nGenerate", "Create Content"),
                    ("♛\nCreator" if is_creator() else "☻\nProfile", profile_page)]
    with st.container(key="mobile_nav"):
        nav_cols = st.columns(5, gap="small")
        for col, (label, destination) in zip(nav_cols, destinations):
            with col:
                st.button(label, key=f"mobile_nav_{destination.replace(' ', '_')}",
                          type="primary" if current == destination else "secondary",
                          use_container_width=True, on_click=set_page, args=(destination,))


def search_submit() -> None:
    query = st.session_state.get("global_search", "").strip()
    if query:
        st.session_state.pop("result", None)
        st.session_state.pop("error", None)
        st.session_state.prefill_topic = query
        st.session_state.page = "Create Content"


def toggle_theme() -> None:
    st.session_state["light_mode"] = not st.session_state.get("light_mode", False)


def show_notifications() -> None:
    pending = [title for idx, (title, _, _) in enumerate(PLAN_ITEMS)
               if not st.session_state.get(f"plan_done_{idx}", False)]
    if pending:
        summary = ", ".join(title.split(" — ")[0] for title in pending[:3])
        more = f" (+{len(pending) - 3} more)" if len(pending) > 3 else ""
        st.toast(f"Still on your plan: {summary}{more}.", icon="🔔")
    else:
        st.toast("All today's study sessions are complete. Nice work!", icon="✅")



def save_quiz_score() -> None:
    score = int(st.session_state.get("quiz_score_input", 0))
    st.session_state["profile_score"] = score
    st.session_state["profile_score_setting"] = score
    st.session_state["profile_has_score"] = True
    result = st.session_state.get("result", {})
    attempts = st.session_state.setdefault("quiz_attempts", [])
    now = datetime.now()
    attempts.insert(0, {
        "topic": result.get("topic", "Practice questions"),
        "score": st.session_state["profile_score"],
        "when": now.strftime("%I:%M %p"),
        "date": now.date().isoformat(),
    })
    del attempts[20:]
    st.toast("Score saved. Your next lesson will adjust to it.", icon="✅")


def sync_profile_score() -> None:
    """Copy the Settings slider value into the learner profile."""
    st.session_state["profile_score"] = int(st.session_state.get("profile_score_setting", 65))
    st.session_state["profile_has_score"] = True


def gemini_error_message(exc: Exception) -> str:
    """Return a useful, sanitized message without exposing request details or credentials."""
    error = str(exc).upper()
    if "401" in error or "API_KEY_INVALID" in error or "UNAUTHENTICATED" in error:
        return "Google rejected the key. Check that it was copied correctly and is still active."
    if "403" in error or "PERMISSION_DENIED" in error:
        return "Google received the key, but this project does not have permission to use the API."
    if "429" in error or "RESOURCE_EXHAUSTED" in error:
        return "Google received the key, but the project has reached a request or quota limit."
    if "503" in error or "UNAVAILABLE" in error:
        return "Google's model service is temporarily unavailable. Try the check again later."
    return "Connection check failed. Verify the key and network, then retry."


def check_gemini_connection() -> None:
    """Check whether the configured Gemini key is recognized without showing it."""
    if not (local_key_setup_allowed() or is_creator()):
        st.session_state["api_check_result"] = (
            "error", "Only the verified creator account can inspect the AI connection."
        )
        return
    api_key = get_key().strip()
    if not api_key:
        st.session_state["api_check_result"] = ("error", "No Gemini key is loaded by this app yet.")
        return
    try:
        model_name = check_api_key(api_key)
        if not model_name:
            st.session_state["api_check_result"] = ("error", "Google returned no text-generation models for this key.")
        else:
            st.session_state["api_check_result"] = (
                "success", f"Google recognized the key and listed a text-generation model: {model_name}."
            )
    except Exception as exc:
        st.session_state["api_check_result"] = ("error", gemini_error_message(exc))


def save_and_check_local_key() -> None:
    """Save and verify a key only when the app is being used on this computer."""
    if not local_key_setup_allowed():
        st.session_state["api_check_result"] = (
            "error", "For safety, local key setup is available only in private development mode."
        )
        return
    api_key = st.session_state.get("local_gemini_key_input", "").strip()
    if not api_key:
        st.session_state["api_check_result"] = ("error", "Paste your Gemini key in the password field first.")
        return
    try:
        save_local_key_file(api_key)
    except OSError:
        st.session_state["api_check_result"] = ("error", "Could not save the key locally. Check that this project folder is writable.")
        return
    st.session_state["runtime_gemini_key"] = api_key
    st.session_state["local_gemini_key_input"] = ""
    try:
        model_name = check_api_key(api_key)
        if model_name:
            st.session_state["api_check_result"] = (
                "success", f"Connected. The key is saved privately on this computer. Text-generation model: {model_name}."
            )
        else:
            st.session_state["api_check_result"] = (
                "error", "The key was saved locally, but Google returned no text-generation models. Check access or try again."
            )
    except Exception as exc:
        st.session_state["api_check_result"] = (
            "error", f"The key was saved locally, but Google could not verify it yet. {gemini_error_message(exc)}"
        )


def topbar() -> None:
    with st.container(key="desktop_topbar"):
        search_col, bell_col, theme_col, profile_col = st.columns([7.2, .55, .55, 1.6], gap="small")
        with search_col:
            st.text_input("Search topics", placeholder="⌕   Search any topic, concept, or question…",
                          label_visibility="collapsed", key="global_search", on_change=search_submit)
        with bell_col:
            pending_count = sum(not st.session_state.get(f"plan_done_{idx}", False) for idx in range(len(PLAN_ITEMS)))
            st.button(f"🔔 {pending_count}", key="notifications", help="Show unfinished study-plan reminders", on_click=show_notifications, use_container_width=True)
        with theme_col:
            st.button("☼" if st.session_state.get("light_mode", False) else "☾", key="theme_toggle",
                      help="Toggle light or dark appearance", on_click=toggle_theme, use_container_width=True)
        with profile_col:
            profile_text = f"👤 {current_user_name().split()[0]}"
            if is_creator():
                profile_text += "  ✦ PREMIUM"
            st.button(profile_text, key="open_profile", help="Open your account",
                      on_click=set_page, args=(profile_destination(),), use_container_width=True)
def render_generator(title: str, subtitle: str, preset: str | None = None, label: str = "Topic",
                    placeholder: str = "For example: process scheduling, neural networks, or photosynthesis",
                    default_type: str | None = None) -> None:
    heading(title, subtitle)
    level, score, reason = profile()
    st.markdown(f"<div class='level {level}'>✦ &nbsp; Recommended difficulty: <b>{level}</b></div>", unsafe_allow_html=True)
    if reason:
        st.caption(reason)
    initial = st.session_state.pop("prefill_topic", "")
    chosen_type = st.session_state.pop("prefill_type", default_type or "Explanation")
    form_id = "create_" + title.lower().replace(" ", "_").replace("&", "and")
    st.selectbox("AI source", list(AI_MODES), key="generation_mode",
                 help="Offline AI runs on the app server. Automatic uses it when installed; otherwise it tries Gemini.")
    if configured_local_model() in local_models():
        st.caption("Offline AI is ready on this server. It can generate new answers without Gemini or internet access.")
    else:
        st.caption("Offline AI is not running on this server yet. Online generation needs the internet.")
    uploaded = None
    with st.form(form_id):
        topic = st.text_input(label, value=initial, placeholder=placeholder, max_chars=160)
        if preset:
            kind = preset
            st.caption(f"{kind} · tailored to {level}")
        else:
            index = CONTENT_TYPES.index(chosen_type) if chosen_type in CONTENT_TYPES else 0
            kind = st.selectbox("What should LearnWave create?", CONTENT_TYPES, index=index)
        uploaded = st.file_uploader("Optional: add study notes (.txt or .md)", type=["txt", "md"])
        st.caption("Uploaded text is used only when you press Generate; this app does not save the file.")
        submitted = st.form_submit_button("✦  Generate for me", type="primary", use_container_width=True)
    if submitted:
        prompt_topic = topic.strip()
        allow_generation = True
        if uploaded:
            if uploaded.size > MAX_UPLOAD_BYTES:
                st.warning("Please upload notes smaller than 1 MB.")
                allow_generation = False
            elif get_key() or configured_local_model() in local_models():
                reference = uploaded.getvalue().decode("utf-8", errors="replace")[:8000]
                prompt_topic = f"{prompt_topic or 'Create learning material from these notes'}" + chr(10) * 2 + f"Reference notes from {uploaded.name}:" + chr(10) + reference
            else:
                st.warning("I can't read the uploaded notes in preview mode. Configure the server-side Gemini key before generating from a file.")
                allow_generation = False
        if allow_generation:
            generate(prompt_topic, kind, level, score)
    show_result()
def home() -> None:
    level, score, _ = profile()
    metrics = learning_metrics()
    chart_path, chart_circles, week_labels = weekly_activity_plot()
    main, rail = st.columns([2.05, 1], gap="medium")
    with main:
        st.markdown(f"""
        <div class="hero"><div class="hero-top">✧ &nbsp; GOOD AFTERNOON, KRISH &nbsp; 👋<br><small>{metrics['lessons']} lessons generated in this session · keep your momentum going.</small></div>
          <div class="hero-copy"><div class="hero-tag">YOUR AI-POWERED LEARNING COMPANION</div>
          <h1>Personalized Learning<br>for a <span>Brighter Future</span></h1>
          <p>Your AI-powered learning companion — understand faster, learn smarter, achieve more.</p></div>
          <div class="hero-quote">Better<br>than<br>Yesterday</div>
          <svg class="hero-art" viewBox="0 0 680 310" aria-hidden="true">
            <defs>
              <linearGradient id="sunsetSky" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#dd83d6"/><stop offset=".48" stop-color="#f47eae"/><stop offset="1" stop-color="#5646aa"/></linearGradient>
              <linearGradient id="farPeak" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#9877dc"/><stop offset="1" stop-color="#344a9a"/></linearGradient>
              <linearGradient id="nearPeak" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#303f88"/><stop offset="1" stop-color="#09172f"/></linearGradient>
              <linearGradient id="foreground" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#202a68"/><stop offset="1" stop-color="#080f26"/></linearGradient>
            </defs>
            <circle cx="474" cy="112" r="68" fill="#ff91ca" opacity=".17"/>
            <circle cx="474" cy="112" r="34" fill="#ffe6bc"/>
            <path d="M330 103c38-31 72-25 97-9 23-30 63-35 89-8 31-15 73-2 84 23H325z" fill="#fa9bd0" opacity=".52"/>
            <path d="M275 137c31-22 62-20 83-5 26-28 63-26 84-3 33-19 74-8 88 15H267z" fill="#a684dc" opacity=".55"/>
            <g fill="#fff1f8" opacity=".85"><circle cx="375" cy="48" r="2"/><circle cx="427" cy="35" r="1.6"/><circle cx="525" cy="52" r="1.8"/><circle cx="584" cy="93" r="1.5"/><circle cx="350" cy="80" r="1.3"/><path d="M555 36h7m-3.5-3.5v7" stroke="#fff" stroke-width="1"/></g>
            <path d="M185 310 325 155l63 75 91-133 121 142 54-74 112 145z" fill="url(#farPeak)" opacity=".9"/>
            <path d="m422 138 57-41 34 69-31-17-20 23-18-29z" fill="#f8d5f1" opacity=".48"/>
            <path d="m243 310 110-122 66 68 90-104 112 130 69-61v89z" fill="url(#nearPeak)"/>
            <path d="M0 310 185 220l86 56 103-83 105 117z" fill="#111d4b"/>
            <path d="m385 193 34-5 14 17-22-8-20 11z" fill="#9295dc" opacity=".38"/>
            <path d="M0 285 95 251l75 32 83-28 83 55H0z" fill="url(#foreground)"/>
            <path d="M280 310 410 250l84 31 75-40 111 69z" fill="url(#foreground)"/>
            <g fill="#050b1b">
              <circle cx="491" cy="192" r="6"/>
              <path d="m486 200 11 0 7 27-7 3-5-18-5 18-8-2z"/>
              <path d="m485 205-10 13 4 4 12-11zm10 1 10 13 5-3-10-16z"/>
              <path d="m491 227-8 26 5 2 7-17 5 18 6-2-7-27z"/>
              <path d="m479 200 8-5 6 16-12 2z" fill="#8f49dd"/>
            </g>
            <path d="M0 282c82-31 139-25 214 7s153 27 230-2 154-32 236-5v28H0z" fill="#081329" opacity=".88"/>
          </svg>
          <div class="hero-stats">
            <div><i>◈</i><b>{metrics['topics']}</b><small>Topics explored</small></div>
            <div><i>✦</i><b>{metrics['lessons']}</b><small>Lessons generated</small></div>
            <div><i>🏆</i><b>{metrics['quizzes']}</b><small>Quiz scores saved</small></div>
            <div><i>◉</i><b>{metrics['plan_percent']}%</b><small>Today's plan done</small></div>
          </div>
        </div>""", unsafe_allow_html=True)
        with st.container(key="mobile_generate_cta"):
            st.button("✧  Generate New Content", key="mobile_generate_content",
                      type="primary", on_click=set_page, args=("Create Content",),
                      use_container_width=True)
        with st.container(key="continue_learning_bar", border=True):
            prompt_col, cta_col = st.columns([4, 1.15], gap="small")
            with prompt_col:
                st.markdown(
                    "<div class='continue-copy'><b>Ready for your next lesson?</b>"
                    "<span>Continue with Operating Systems · Process Scheduling</span></div>",
                    unsafe_allow_html=True,
                )
            with cta_col:
                st.button("Continue  →", key="hero_continue", type="primary",
                          on_click=study_topic, args=("Operating Systems",), use_container_width=True)

        render_home_assistant("mobile")
        section("Quick actions", "Shortcuts to your next learning step", "View all")
        action_cols = st.columns(6, gap="small")
        actions = [
            ("Generate New Content", "Ask anything, get instant explanations", "Create Content", ""),
            ("My Learning History", "See what you have learned", "Progress & Analytics", "blue"),
            ("My Progress", "Track your goals", "Progress & Analytics", "teal"),
            ("Create Flashcards", "Turn notes into quick revision", "Notes & Flashcards", "purple"),
            ("Take a Quiz", "Test your knowledge", "Quizzes & Practice", "orange"),
            ("Settings", "Customize your experience", "Settings", "blue"),
        ]
        for col, (title, desc, dest, tone) in zip(action_cols, actions):
            with col:
                with st.container(border=True):
                    st.markdown(f"<div class='icon {tone}'>✧</div><div class='actiontitle'>{title}</div><span class='muted'>{desc}</span>", unsafe_allow_html=True)
                    st.button("Open  →", key=f"quick_{title}", use_container_width=True, on_click=set_page, args=(dest,))

        section("Your learning path", "Small steps add up to real progress", "Full path")
        path_cols = st.columns(4, gap="small")
        for idx, (col, item) in enumerate(zip(path_cols, SUBJECTS)):
            name, done, next_topic, tone = item
            pct = [50, 40, 25, 8][idx]
            with col:
                with st.container(border=True):
                    st.markdown(f"<div class='pathhead {tone}'><span>✦</span><b>{name}</b></div><span class='muted'>{done}</span><div class='meter'><i style='width:{pct}%'></i></div><span class='muted'>Next: {next_topic}</span>", unsafe_allow_html=True)
                    st.button("Continue  →", key=f"continue_{idx}", use_container_width=True, on_click=study_topic, args=(next_topic,))

        with st.container(key="mobile_showcase"):
            motivation_col, recommendation_col = st.columns([.9, 1.1], gap="small")
            with motivation_col:
                with st.container(key="mobile_motivation"):
                    with st.container(border=True):
                        st.markdown("<div class='section'><div><b>Today's Motivation</b><small>A reminder for your learning journey</small></div></div><p class='mobile-quote'>“Discipline today builds the freedom of tomorrow.”</p><div class='mobile-attribution'>— Krish</div>", unsafe_allow_html=True)
                        st.progress(metrics["plan_percent"] / 100)
            with recommendation_col:
                with st.container(key="mobile_recommendations"):
                    with st.container(border=True):
                        st.markdown("<div class='section'><div><b>Recommended for You</b><small>Picked for your learning path</small></div></div>", unsafe_allow_html=True)
                        for idx, (icon, title, meta) in enumerate([
                            ("🐍", "Python for AI/ML", "Beginner · 4.5 ★ · 12.4k learners"),
                            ("▦", "System Design Basics", "Intermediate · 4.2 ★ · 8.7k learners"),
                            ("◇", "Web Security & Browser Extensions", "Intermediate · 4.6 ★ · 6.2k learners"),
                            ("⌘", "Competitive Programming", "Advanced · 4.3 ★ · 10.1k learners"),
                        ]):
                            info_col, start_col = st.columns([4, 1], gap="small")
                            with info_col:
                                st.markdown(f"<div class='recrow'><span>{icon}</span><div><b>{title}</b><small>{meta}</small></div></div>", unsafe_allow_html=True)
                            with start_col:
                                st.button("Start", key=f"mobile_recommend_{idx}", on_click=study_topic, args=(title,), use_container_width=True)

        activity_col, progress_col, resource_col = st.columns([1.15, 1, .95], gap="small")
        with activity_col:
            with st.container(key="home_activity", border=True):
                section("Recent activity", "Your latest learning moments")
                history = st.session_state.get("activity", [])
                if history:
                    for item in history[:5]:
                        st.markdown(f"<div class='activity'><em>✦</em><span><b>{item['topic']}</b><small>{item['kind']} · Today, {item['when']}</small></span></div>", unsafe_allow_html=True)
                else:
                    st.info("Your generated lessons will appear here after you create your first one.")
        with progress_col:
            with st.container(key="home_progress", border=True):
                section("Progress overview", "Your session activity")
                st.markdown(f"""
                <div class="progress-summary"><div class="donut" style="background:conic-gradient(#1de0e2 0 {metrics['plan_percent']}%,#132b4b {metrics['plan_percent']}% 100%)"><b>{metrics['plan_percent']}%<small>today's plan</small></b></div>
                <div class="legend"><span><i class="blue-dot"></i> Topics explored <b>{metrics['topics']}</b></span>
                <span><i class="purple-dot"></i> Lessons generated <b>{metrics['lessons']}</b></span>
                <span><i class="teal-dot"></i> Quiz scores saved <b>{metrics['quizzes']}</b></span>
                <span><i class="orange-dot"></i> Plan tasks done <b>{metrics['plan_completed']}/{metrics['plan_total']}</b></span></div></div>
                <svg class="chart" viewBox="0 0 260 72" role="img" aria-label="Weekly progress trend">
                  <defs><linearGradient id="chartline" x1="0" x2="1"><stop stop-color="#14d6e4"/><stop offset="1" stop-color="#ad4dff"/></linearGradient></defs>
                  <path d="{chart_path}" fill="none" stroke="url(#chartline)" stroke-width="3"/>
                  <g fill="#62d9ff">{chart_circles}</g>
                </svg><div class="weekdays">{week_labels}</div>
                """, unsafe_allow_html=True)
        with resource_col:
            with st.container(key="home_resources", border=True):
                section("Study resources", "Useful for your courses")
                for idx, (icon, title, desc, target_page, topic, kind) in enumerate([
                    ("▣", "Important notes", "Handwritten style & PDF", "Notes & Flashcards", "Operating Systems", "Revision notes"),
                    ("▤", "Flashcards", "Quick revision", "Notes & Flashcards", "Operating Systems", "Flashcards"),
                    ("▧", "Past year questions", "Practice for exams", "Quizzes & Practice", "Operating Systems", "Practice questions"),
                    ("▣", "E-books & links", "Curated resources", "Study Resources", "", None),
                ]):
                    resource_col, open_col = st.columns([4, 1], gap="small")
                    with resource_col:
                        st.markdown(f"<div class='resource-row'><i>{icon}</i><span><b>{title}</b><small>{desc}</small></span></div>", unsafe_allow_html=True)
                    with open_col:
                        st.button("→", key=f"home_resource_{idx}", on_click=open_resource,
                                  args=(target_page, topic, kind), use_container_width=True)
                st.button("View all resources  →", key="all_resources", use_container_width=True, on_click=set_page, args=("Study Resources",))

    with rail:
        render_home_assistant("desktop_home")

        with st.container(border=True):
            section("Study plan", "Today · your daily checklist", "View calendar")
            for idx, (title, time, tone) in enumerate(PLAN_ITEMS):
                check_col, task_col = st.columns([.13, .87], gap="small")
                with check_col:
                    st.checkbox("Done", key=f"plan_done_{idx}", label_visibility="collapsed")
                with task_col:
                    icon = "✓" if st.session_state.get(f"plan_done_{idx}", False) else "◉"
                    st.markdown(f"<div class='planrow'><span class='{tone}'>{icon}</span><div><b>{title}</b><small>{time}</small></div></div>", unsafe_allow_html=True)
            completed = sum(bool(st.session_state.get(f"plan_done_{idx}", False)) for idx in range(len(PLAN_ITEMS)))
            st.markdown(f"<small class='muted'>{completed}/{len(PLAN_ITEMS)} tasks completed</small>", unsafe_allow_html=True)
            st.progress(completed / len(PLAN_ITEMS))

        with st.container(key="desktop_home_recommendations", border=True):
            section("Recommended for you", "Picked for your learning path", "View all")
            for idx, (icon, title, meta) in enumerate([
                ("🐍", "Python for AI/ML", "Beginner · 4.5 ★ · 12.4k learners"),
                ("▦", "System Design Basics", "Intermediate · 4.2 ★ · 8.7k learners"),
                ("◇", "Web Security & Browser Extensions", "Intermediate · 4.6 ★ · 6.2k learners"),
                ("⌘", "Competitive Programming", "Advanced · 4.3 ★ · 10.1k learners"),
            ]):
                info_col, start_col = st.columns([4, 1.1], gap="small")
                with info_col:
                    st.markdown(f"<div class='recrow'><span>{icon}</span><div><b>{title}</b><small>{meta}</small></div></div>", unsafe_allow_html=True)
                with start_col:
                    st.button("Start", key=f"recommend_{idx}", on_click=study_topic, args=(title,), use_container_width=True)
            st.button("Browse courses  →", key="browse_courses", use_container_width=True, on_click=set_page, args=("Courses",))
    st.markdown("<div class='home-footer'><span>✧</span><b>Your goals. Our mission.</b><small>Learn smarter · Build skills · Create your future</small><i>“The expert in anything was once a beginner.” — Helen Keller</i></div>", unsafe_allow_html=True)


def render_home_assistant(instance: str) -> None:
    """Render the same usable assistant in the desktop rail or phone flow."""
    wrapper_key = "mobile_assistant" if instance == "mobile" else "desktop_home_assistant"
    with st.container(key=wrapper_key):
        with st.container(border=True):
            st.markdown("""
            <div class="assistant-head"><div><div class="railtitle">✧ &nbsp; AI Learning Assistant <span class="beta">BETA</span></div>
            <span class="muted">Ask anything, get instant, accurate answers.</span></div>
            <div class="robot-scene"><div class="robot-antenna"></div><div class="robot-ear"></div><div class="robot-face"><i></i><b></b></div><div class="robot-body">⌨</div></div></div>
            """, unsafe_allow_html=True)
            prompts = ["Explain FCFS with an example", "Give me 10 SJF questions",
                       "Create a study plan for OS", "Compare B-tree and B+ tree"]
            for idx, prompt in enumerate(prompts):
                st.button("☑  " + prompt, key=f"{instance}_suggestion_{idx}", use_container_width=True,
                          on_click=ask_suggestion, args=(prompt,))
            with st.form(f"{instance}_home_ask"):
                question = st.text_input("Question", placeholder="Type your question…",
                                         label_visibility="collapsed", key=f"{instance}_home_question")
                ask = st.form_submit_button("Send  ↗", key=f"{instance}_home_ask_submit",
                                            type="primary", use_container_width=True)
            if ask:
                level, score, _ = profile()
                generate(question, "Explanation", level, score)
                st.session_state.home_show_result = True
            if st.session_state.get("home_show_result", False) and (st.session_state.get("result") or st.session_state.get("error")):
                show_result()
            st.button("Open full assistant  →", key=f"{instance}_open_ai_home", use_container_width=True,
                      on_click=set_page, args=("AI Assistant",))


def learning_path() -> None:
    heading("My Learning Path", "Your subjects, next steps, and recommended difficulty.")
    level, _, reason = profile()
    st.markdown(f"<div class='level {level}'>✦ &nbsp; Recommended difficulty: <b>{level}</b></div>", unsafe_allow_html=True)
    if reason: st.caption(reason)
    cols = st.columns(2, gap="medium")
    for i, (name, done, next_topic, tone) in enumerate(SUBJECTS):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"<div class='pathhead {tone}'><span>✦</span><b>{name}</b></div><h3>{done}</h3><span class='muted'>Next lesson: {next_topic}</span>", unsafe_allow_html=True)
                st.progress([.5, .4, .25, .08][i])
                st.button("Continue learning  →", key=f"pathpage_{i}", type="primary", on_click=study_topic, args=(next_topic,))


def courses() -> None:
    heading("Courses", "Choose a subject and start a lesson at your personalized level.")
    cols = st.columns(2, gap="medium")
    for i, (name, done, next_topic, tone) in enumerate(SUBJECTS):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"<div class='pathhead {tone}'><span>✧</span><b>{name}</b></div><span class='muted'>{done} · Next: {next_topic}</span>", unsafe_allow_html=True)
                st.button("Study this subject  →", key=f"course_{i}", type="primary", on_click=study_topic, args=(name,))


def progress_page() -> None:
    heading("Progress & Analytics", "See how you are progressing and decide what to review next.")
    metrics = learning_metrics()
    cols = st.columns(3)
    values = [str(metrics["topics"]), str(metrics["lessons"]), str(metrics["quizzes"])]
    labels = ["Topics explored", "Lessons generated", "Quiz scores saved"]
    for col, value, label in zip(cols, values, labels):
        with col: st.markdown(f"<div class='stat'><span>↗</span><strong>{value}</strong><small>{label}</small></div>", unsafe_allow_html=True)
    st.markdown("### Today's study plan")
    st.progress(metrics["plan_percent"] / 100)
    st.caption(f"{metrics['plan_completed']} of {metrics['plan_total']} sessions marked complete")
    st.markdown("### Saved quiz scores")
    attempts = st.session_state.get("quiz_attempts", [])
    if attempts:
        for attempt in attempts[:5]:
            st.markdown(f"**{attempt['topic']}** <span class='muted'>{attempt['score']}%</span>", unsafe_allow_html=True)
            st.progress(attempt["score"] / 100)
    else:
        st.info("Generate a practice set, try it, and save your self-assessed score to see quiz progress here.")
    st.markdown("### Learning history")
    history = st.session_state.get("activity", [])
    if history:
        for item in history:
            when = "Today" if item.get("date") == date.today().isoformat() else item.get("date", "This session")
            st.markdown(f"- **{item['topic']}** — {item['kind']} · {when}, {item['when']}")
    else: st.info("Generate a lesson or practice set and it will appear in your history for this session.")


def study_plan_page() -> None:
    heading("Study Plan", "Check off today's sessions and shape a realistic weekly routine.")
    today, week, month = st.tabs(["Today", "This Week", "This Month"])
    with today:
        for idx, (title, time, tone) in enumerate(PLAN_ITEMS):
            left, right = st.columns([.12, .88], gap="small")
            with left:
                st.checkbox("Done", key=f"plan_done_{idx}", label_visibility="collapsed")
            with right:
                st.markdown(f"**{title}**<br><span class='muted'>{time}</span>", unsafe_allow_html=True)
        completed = sum(bool(st.session_state.get(f"plan_done_{idx}", False)) for idx in range(len(PLAN_ITEMS)))
        st.progress(completed / len(PLAN_ITEMS))
        st.caption(f"{completed} of {len(PLAN_ITEMS)} sessions completed today")
    with week:
        week_items = [
            ("Monday", "Operating Systems · Process Scheduling"),
            ("Tuesday", "Data Structures · B Tree"),
            ("Wednesday", "Cloud Computing · AWS"),
            ("Thursday", "AI & Software Engineering · Python OOP"),
            ("Friday", "Revision and practice"),
        ]
        for idx, (day, task) in enumerate(week_items):
            st.checkbox(f"{day}  ·  {task}", key=f"week_done_{idx}")
    with month:
        target = st.number_input("Monthly study-session goal", min_value=4, max_value=100, value=20, step=1, key="monthly_goal")
        today_completed = sum(bool(st.session_state.get(f"plan_done_{idx}", False)) for idx in range(len(PLAN_ITEMS)))
        week_completed = sum(bool(st.session_state.get(f"week_done_{idx}", False)) for idx in range(5))
        completed = today_completed + week_completed
        st.markdown(f"**{completed} / {target} sessions logged**")
        st.progress(min(completed / target, 1.0))
        st.caption("Counts checked sessions in the current browser session. Progress resets when the session ends.")


def resources() -> None:
    heading("Study Resources", "A focused collection of tools for your current subjects.")
    items = [
        ("Important notes", "Generate concise explanations and key definitions.", "Notes & Flashcards", "Revision notes"),
        ("Flashcards", "Create quick recall prompts from any topic.", "Notes & Flashcards", "Flashcards"),
        ("Past year questions", "Practice exam-style questions for a topic.", "Quizzes & Practice", "Practice questions"),
        ("Study guides", "Ask the assistant to plan your next study session.", "AI Assistant", "Explanation"),
    ]
    cols = st.columns(2, gap="medium")
    for i, (title, desc, dest, kind) in enumerate(items):
        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(f"### ✧ {title}<span class='muted'>{desc}</span>", unsafe_allow_html=True)
                st.button("Open resource  →", key=f"resource_{i}",
                          on_click=open_resource, args=(dest, title, kind))


def settings() -> None:
    heading("Settings", "Adjust how LearnWave personalizes content for you.")
    if login_required():
        with st.container(border=True):
            st.markdown("### Account")
            st.write("Signed in as", current_user_name())
            st.caption(user_claim("email"))
            if is_creator():
                st.success("✦ Premium Creator mode is active for this verified Google account.")
            else:
                st.caption("Your learning activity stays in this browser session. Creator tools and service configuration are private.")
            st.button("Log out", key="logout_account", on_click=st.logout)
    with st.container(border=True):
        st.markdown("### Learner profile")
        st.write("Choose your starting confidence. A recent quiz score can simplify or increase content difficulty by one level.")
        st.selectbox("Starting confidence", LEVELS, key="profile_level")
        st.checkbox("Use a recent quiz score to adjust difficulty", key="profile_has_score")
        if st.session_state.get("profile_has_score", True):
            st.session_state.setdefault("profile_score_setting", int(st.session_state.get("profile_score", 65)))
            st.slider("Most recent quiz score", 0, 100, key="profile_score_setting", on_change=sync_profile_score)
        level, _, reason = profile()
        st.markdown(f"<div class='level {level}'>✦ &nbsp; Current recommendation: <b>{level}</b></div>", unsafe_allow_html=True)
        if reason: st.caption(reason)
    with st.container(border=True):
        st.markdown("### AI connection")
        can_manage_ai_connection = local_key_setup_allowed() or is_creator()
        if not can_manage_ai_connection:
            st.caption("The AI service is configured privately by the app owner. API keys, server settings, and source code are not available to learner accounts.")
        else:
            key_loaded = bool(get_key())
            local_setup = local_key_setup_allowed()
            if key_loaded:
                st.success("A Gemini key is loaded in this app process.")
            else:
                st.info("Gemini is not configured. You can still generate lessons with Offline AI when its local server is running.")
            if local_setup:
                with st.expander("Paste or replace your Gemini key on this computer", expanded=not key_loaded):
                    st.text_input(
                        "Gemini API key", type="password", key="local_gemini_key_input",
                        placeholder="Paste the new key from Google AI Studio",
                        help="This password field is available only while running LearnWave locally in development mode.",
                    )
                    st.caption("Save and check stores the key in the ignored local secrets file, then asks Google to verify it. The key is never displayed.")
                    st.button("Save & check Gemini key", key="save_local_gemini_key", on_click=save_and_check_local_key)
            elif login_required():
                st.caption("Manage Gemini and Google credentials from the host's private Secrets manager. They cannot be changed through the public app.")
            else:
                st.caption("For a public deployment, add GEMINI_API_KEY in the hosting provider's server-side Secrets settings. Do not enter it into a public app page or put it in source code.")
            st.button("Check Gemini key access", key="check_gemini", on_click=check_gemini_connection)
            if result := st.session_state.get("api_check_result"):
                if result[0] == "success":
                    st.success(result[1])
                else:
                    st.error(result[1])
        st.caption("The curriculum completion fractions and course recommendations are sample data. Generated lessons, saved quiz scores, and study-plan checks are tracked in this browser session only.")


def creator_console() -> None:
    """A private owner dashboard; server code and infrastructure stay protected."""
    if not is_creator():
        st.error("Creator Console is available only to the verified creator Google account.")
        return
    heading("Creator Console", "Private controls for the LearnWave owner.")
    with st.container(border=True):
        st.success("✦ Premium Creator mode is active.")
        st.write("Signed in as", current_user_name())
        st.caption(user_claim("email"))
        st.caption("Your role is based on a server-side email allowlist. A learner cannot enable it from the browser.")
    with st.container(border=True):
        st.markdown("### Safe creator controls")
        st.write("Use Settings to adjust learner personalization and use the dashboard to test the experience a student receives.")
        st.button("Open Settings", key="creator_open_settings", on_click=set_page, args=("Settings",))
    with st.container(border=True):
        st.markdown("### Private server controls")
        st.write("Gemini keys, Google OAuth secrets, backend code, frontend code, and deployment settings stay outside the public app.")
        st.caption("Edit source in VS Code, keep the repository private, change secrets in the host's Secrets manager, then redeploy. A public web code editor would expose the server and is intentionally not provided.")


def main() -> None:
    st.session_state.setdefault("page", "Home")
    st.session_state.setdefault("profile_level", "Intermediate")
    st.session_state.setdefault("profile_has_score", True)
    st.session_state.setdefault("profile_score", 65)
    st.session_state.setdefault("activity", [])
    st.session_state.setdefault("lessons_generated", len(st.session_state["activity"]))
    st.session_state.setdefault("topics_seen", list(dict.fromkeys(item.get("topic", "") for item in st.session_state["activity"] if item.get("topic"))))
    st.session_state.setdefault("quiz_attempts", [])
    st.session_state.setdefault("daily_activity", {})
    st.session_state.setdefault("light_mode", False)
    if st.session_state.get("plan_state_version") != 2:
        for task_index in range(len(PLAN_ITEMS)):
            st.session_state[f"plan_done_{task_index}"] = False
        st.session_state["plan_state_version"] = 2
    for task_index in range(len(PLAN_ITEMS)):
        st.session_state.setdefault(f"plan_done_{task_index}", False)
    css()
    require_demo_password()
    require_authenticated_user()
    sidebar()
    mobile_topbar()
    topbar()
    page = st.session_state.get("page", "Home")
    if page == "Home": home()
    elif page == "My Learning Path": learning_path()
    elif page == "Courses": courses()
    elif page == "AI Assistant": render_generator("AI Learning Assistant", "Ask anything you are studying and get an explanation for your current level.", "Explanation", "Your question")
    elif page == "Progress & Analytics": progress_page()
    elif page == "Notes & Flashcards": render_generator("Notes & Flashcards", "Create concise revision notes and interactive flashcards.", default_type="Flashcards")
    elif page == "Quizzes & Practice": render_generator("Quizzes & Practice", "Practice questions adapt to your recommended difficulty.", "Practice questions", "Quiz topic")
    elif page == "Doubt Solver": render_generator("Doubt Solver", "Describe the concept or question that is confusing you.", "Explanation", "What are you stuck on?", "For example: Why does binary search need sorted data?")
    elif page == "Study Resources": resources()
    elif page == "Study Plan": study_plan_page()
    elif page == "Settings": settings()
    elif page == "Creator Console": creator_console()
    elif page == "Create Content": render_generator("Generate new content", "Choose a topic and let LearnWave create your next lesson.")
    else: home()
    mobile_navigation()


if __name__ == "__main__":
    main()




