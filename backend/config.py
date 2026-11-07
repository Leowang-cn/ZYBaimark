from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# 平台注入的环境变量优先，不被 .env 覆盖
load_dotenv(ROOT / ".env", override=False)


class ConfigError(RuntimeError):
    pass


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None or v == "" else v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name) or str(default)
    try:
        v = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} 必须是整数") from e
    if not lo <= v <= hi:
        raise ConfigError(f"{name} 必须在 {lo}-{hi} 之间")
    return v


@dataclass(frozen=True)
class Settings:
    app_env: str
    secret_key: str
    app_password: str
    database_url: str
    data_dir: Path
    storage_provider: str
    cos_region: str
    cos_bucket: str
    cos_prefix: str
    cos_secret_id: str
    cos_secret_key: str
    cos_session_token: str
    ai_base_url: str
    ai_model: str
    ai_api_key: str
    asr_base_url: str
    asr_model: str
    asr_api_key: str
    ffmpeg: str
    ffprobe: str
    worker_concurrency: int
    max_video_seconds: int
    max_ids_per_task: int
    cookie_secure: bool
    trusted_proxy_cidrs: str
    ctp_base_url: str
    login_timeout_s: int
    max_login_sessions: int
    cookie_ttl_hours: int

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR") or ROOT / "data")
    ai_base = (os.getenv("AI_BASE_URL") or "https://llm.baifentan.com/openproxy/rp/v1").rstrip("/")
    s = Settings(
        app_env=(os.getenv("APP_ENV") or "development").strip().lower(),
        secret_key=os.getenv("SECRET_KEY", ""),
        app_password=os.getenv("APP_PASSWORD", ""),
        database_url=os.getenv("DATABASE_URL") or f"sqlite:///{data_dir / 'app.sqlite'}",
        data_dir=data_dir,
        storage_provider=(os.getenv("STORAGE_PROVIDER") or "local").strip().lower(),
        cos_region=os.getenv("COS_REGION", ""),
        cos_bucket=os.getenv("COS_BUCKET", ""),
        cos_prefix=os.getenv("COS_PREFIX", ""),
        cos_secret_id=os.getenv("COS_SECRET_ID", ""),
        cos_secret_key=os.getenv("COS_SECRET_KEY", ""),
        cos_session_token=os.getenv("COS_SESSION_TOKEN", ""),
        ai_base_url=ai_base,
        ai_model=os.getenv("AI_MODEL") or "gpt-5.2",
        ai_api_key=os.getenv("AI_API_KEY", ""),
        asr_base_url=(os.getenv("ASR_BASE_URL") or ai_base).rstrip("/"),
        asr_model=os.getenv("ASR_MODEL") or "whisper-1",
        asr_api_key=os.getenv("ASR_API_KEY") or os.getenv("AI_API_KEY", ""),
        ffmpeg=os.getenv("FFMPEG_BIN") or "ffmpeg",
        ffprobe=os.getenv("FFPROBE_BIN") or "ffprobe",
        worker_concurrency=_int("WORKER_CONCURRENCY", 2, 1, 8),
        max_video_seconds=_int("MAX_VIDEO_SECONDS", 2400, 60, 7200),
        max_ids_per_task=_int("MAX_IDS_PER_TASK", 30, 1, 200),
        cookie_secure=_bool("COOKIE_SECURE", False),
        trusted_proxy_cidrs=os.getenv("TRUSTED_PROXY_CIDRS", ""),
        ctp_base_url=(os.getenv("CTP_BASE_URL") or "https://iot-admin.yukework.com").rstrip("/"),
        login_timeout_s=_int("LOGIN_TIMEOUT_SECONDS", 300, 60, 1800),
        max_login_sessions=_int("MAX_LOGIN_SESSIONS", 2, 1, 10),
        cookie_ttl_hours=_int("CTP_COOKIE_TTL_HOURS", 24, 1, 720),
    )
    validate(s)
    return s


def validate(s: Settings) -> None:
    errors = []
    if s.app_env not in {"development", "production"}:
        errors.append("APP_ENV 只能是 development 或 production")
    if len(s.secret_key) < 32:
        errors.append("SECRET_KEY 至少 32 个字符")
    if len(s.app_password) < 8:
        errors.append("APP_PASSWORD 至少 8 个字符")
    if s.storage_provider not in {"cos", "local"}:
        errors.append("STORAGE_PROVIDER 只能是 cos 或 local")
    if s.storage_provider == "local" and s.is_production:
        errors.append("生产环境必须使用 STORAGE_PROVIDER=cos")
    if s.storage_provider == "cos":
        for k in ["cos_region", "cos_bucket", "cos_prefix", "cos_secret_id", "cos_secret_key"]:
            if not getattr(s, k):
                errors.append(f"{k.upper()} 未配置")
        if s.cos_bucket and not re.search(r"-\d+$", s.cos_bucket):
            errors.append("COS_BUCKET 必须包含 APPID 后缀")
        parts = [p for p in s.cos_prefix.split("/") if p]
        if not parts or "\\" in s.cos_prefix or any(p in {".", ".."} for p in parts):
            errors.append("COS_PREFIX 必填，且不允许 .、.. 或反斜杠")
    if not s.ai_api_key:
        errors.append("AI_API_KEY 未配置")
    if s.is_production and not s.cookie_secure:
        errors.append("生产环境必须设置 COOKIE_SECURE=true")
    if errors:
        raise ConfigError("配置错误：" + "；".join(errors))


settings: Settings | None = None


def get_settings() -> Settings:
    global settings
    if settings is None:
        settings = load_settings()
    return settings
