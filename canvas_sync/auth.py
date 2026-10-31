"""Token personal: captura local oculta, validación previa y DPAPI de usuario."""
import getpass
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from capture.sources.auth import dpapi
from .api import Client
from .common import SyncError, atomic_bytes, atomic_json, safe_dir

FILE = "canvas-token.dpapi"
# Metadatos NO secretos junto al token: solo vencimiento y momento de guardado.
META_FILE = "token-meta.json"
WARNING_DAYS = 15


def token_path(runtime):
    return safe_dir(runtime, "control", "canvas-sync") / FILE


def expiry_from_input(raw, now=None):
    try:
        naive = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise SyncError("Vencimiento inválido: YYYY-MM-DD HH:MM.") from None
    current = now or datetime.now().astimezone()
    expiry = naive.replace(tzinfo=current.tzinfo)
    if not (current < expiry <= current + timedelta(days=90)):
        raise SyncError("El vencimiento debe ser futuro y no exceder 90 días.")
    return expiry


def save_meta(runtime, expiry, now=None):
    """Guarda solo el vencimiento (nunca material del token) de forma atómica."""
    saved = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    atomic_json(token_path(runtime).with_name(META_FILE),
                {"schema_version": 1, "expires_at": expiry.isoformat(), "saved_at": saved.isoformat()})


def _try_save_meta(runtime, expiry):
    try:
        save_meta(runtime, expiry)
    except (SyncError, OSError, ValueError):
        print("AVISO: no se pudo registrar el vencimiento en los metadatos locales; ejecute status.")


def read_meta(runtime):
    """Lee el vencimiento de los metadatos sin descifrar ni crear directorios."""
    path = Path(runtime) / "control" / "canvas-sync" / META_FILE
    try:
        if path.is_symlink() or not path.is_file():
            raise SyncError("No hay metadatos de vencimiento del token: ejecute status o login en el PC.")
        value = json.loads(path.read_text(encoding="utf-8"))
        expiry = datetime.fromisoformat(value["expires_at"])
        if value.get("schema_version") != 1 or expiry.tzinfo is None:
            raise ValueError("Formato de metadatos")
        return expiry
    except SyncError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SyncError("Metadatos de vencimiento del token ilegibles: ejecute status en el PC.") from None


def notice(expiry, today, now=None):
    """Bloque `{vence, dias_restantes, aviso}`; aviso a 15 días o menos o vencido."""
    days = (expiry.date() - today).days
    if days < 0 or (now is not None and expiry <= now):
        warning = "Token Canvas vencido: ejecute renew."
    elif days <= WARNING_DAYS:
        warning = f"Token Canvas vence en {days} días: prepare la renovación con renew."
    else:
        warning = None
    return {"vence": expiry.date().isoformat(), "dias_restantes": days, "aviso": warning}


def load(runtime):
    if os.name != "nt":
        raise SyncError("Canvas Sync V0 requiere Windows DPAPI.")
    path = token_path(runtime)
    if not path.exists():
        raise SyncError("No existe autorización local: ejecute login.")
    if path.is_symlink():
        raise SyncError("El archivo de autorización está redirigido.")
    try:
        value = json.loads(dpapi(path.read_bytes(), decrypt=True))
        expiry = datetime.fromisoformat(value["expires_at"])
        if (expiry.tzinfo is None or value.get("version") != 1
                or value["host"] != "areandina.instructure.com"
                or not isinstance(value["account_id"], int)
                or not isinstance(value["token"], str) or not value["token"]):
            raise ValueError("Formato de credencial")
        return value, expiry
    except Exception:
        raise SyncError("Autorización local ilegible para este usuario de Windows.") from None


def valid(runtime, now=None):
    data, expiry = load(runtime)
    if expiry <= (now or datetime.now().astimezone()):
        raise SyncError("Token vencido. Ejecute renew.")
    return data, expiry


def hidden_token():
    if not sys.stdin.isatty():
        raise SyncError("Introduzca el token desde una terminal local interactiva.")
    try:
        secret = getpass.getpass("Pega el token Canvas (entrada oculta) y pulsa Enter: ")
    except (EOFError, KeyboardInterrupt):
        raise SyncError("Introducción cancelada.") from None
    return _checked_secret(secret)


def _checked_secret(secret):
    if not isinstance(secret, str) or not secret or secret != secret.strip() or "\r" in secret or "\n" in secret:
        raise SyncError("Token vacío o mal copiado; no se guardó.")
    return secret


def authorize(runtime, renew=False, input_fn=input, secret_fn=hidden_token, client_factory=Client, now=None):
    if os.name != "nt":
        raise SyncError("Canvas Sync V0 requiere Windows DPAPI.")
    path = token_path(runtime)
    if path.exists() and not renew:
        raise SyncError("Ya existe token. Para sustituirlo utilice renew.")
    if renew and not path.exists():
        raise SyncError("No existe token anterior; utilice login.")
    previous = load(runtime)[0] if renew else None
    print("La hora de expiración debe coincidir con la configurada en Canvas (hora local).")
    token = None
    try:
        expiry = expiry_from_input(input_fn("Vencimiento YYYY-MM-DD HH:MM: "), now=now)
        token = secret_fn()
        identity = client_factory(token).profile()
        if previous and identity != previous["account_id"]:
            raise SyncError("El token pertenece a otra cuenta; no se reemplazó el anterior.")
        bundle = {"version": 1, "host": "areandina.instructure.com",
                  "expires_at": expiry.isoformat(), "account_id": identity, "token": token}
        atomic_bytes(path, dpapi(json.dumps(bundle).encode("utf-8")))
    except SyncError:
        raise
    except Exception:
        raise SyncError("Falló la autorización/guardado; la credencial anterior permanece intacta.") from None
    finally:
        token = None
    _try_save_meta(runtime, expiry)
    print("Autorización validada por Canvas y guardada cifrada en el PC.")


def save_token(runtime, token, expiry, *, client_factory=Client, now=None):
    """Entrada no interactiva (asistente): misma validación y guardado DPAPI que login/renew.

    `expiry` usa el formato de la consola (YYYY-MM-DD HH:MM, hora local). Si ya hay token,
    se sustituye como en renew: solo por uno válido de la misma cuenta Canvas.
    """
    secret = _checked_secret(token)
    renew = token_path(runtime).exists()
    authorize(runtime, renew=renew, input_fn=lambda _prompt: expiry, secret_fn=lambda: secret,
              client_factory=client_factory, now=now)


def status(runtime, client_factory=Client, now=None):
    data, expiry = load(runtime)
    try:
        stored = read_meta(runtime)
    except SyncError:
        stored = None
    if stored != expiry:
        _try_save_meta(runtime, expiry)
    current = now or datetime.now().astimezone()
    print("Vencimiento:", expiry.strftime("%Y-%m-%d %H:%M %z"))
    if expiry <= current:
        print("VENCIDO: ejecute renew.")
        return False
    if expiry - current <= timedelta(days=15):
        print("AVISO: quedan 15 días o menos; prepare renovación manual.")
    else:
        print("Vigencia local: correcta.")
    if client_factory(data["token"]).profile() != data["account_id"]:
        raise SyncError("La cuenta Canvas ya no corresponde a la cuenta autorizada.")
    print("API Canvas: autorizada (GET perfil).")
    return True
