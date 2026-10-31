"""Un solo video público de YouTube mediante yt-dlp; nunca usa cookies ni subtítulos automáticos."""
import html
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from ..models import CaptureError
from ..storage.metadata import atomic_json, sha256

HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
MAX_SECONDS = 4 * 3600
ALLOWED_AVAILABILITY = (None, "public", "unlisted")
LIVE_STATES = ("is_live", "is_upcoming", "post_live")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# Base options: one video, no playlist expansion, no account data and no files beyond the requested one.
BASE_OPTIONS = {"noplaylist": True, "quiet": True, "no_warnings": True, "noprogress": True,
                "cachedir": False, "writesubtitles": False, "writeautomaticsub": False}


def _client(options):
    """Seam for tests: yt-dlp is imported only when a YouTube source is used."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise CaptureError("Falta yt-dlp en el entorno de Capture. Instale requirements-capture.txt "
                           "en el Python de Capture y reintente el job.") from exc
    return yt_dlp.YoutubeDL(options)


def is_url(source):
    url = urlparse(str(source))
    return url.scheme in ("http", "https") and (url.hostname or "").lower() in HOSTS


def video_id(source):
    url = urlparse(source)
    host = (url.hostname or "").lower()
    query = parse_qs(url.query)
    if "list" in query or url.path.startswith("/playlist"):
        raise CaptureError("Capture procesa un solo video de YouTube; no listas de reproducción.")
    if host == "youtu.be":
        value = url.path.strip("/")
    elif url.path == "/watch":
        value = query.get("v", [""])[0]
    elif url.path.startswith("/shorts/"):
        value = url.path.removeprefix("/shorts/").strip("/")
    else:
        value = ""
    if not VIDEO_ID.fullmatch(value):
        raise CaptureError("Use una URL de un video de YouTube (watch?v=, youtu.be/ o shorts/).")
    return value


def _failure(exc):
    text = str(exc)
    if re.search(r"sign in|\bage\b|private|members|log ?in|cookies|premium", text, re.I):
        return CaptureError("El video de YouTube requiere iniciar sesión (privado, solo miembros o con "
                            "restricción de edad); Capture no usa cookies ni cuentas.")
    return CaptureError("No se pudo leer el video de YouTube; revise la URL, la conexión y "
                        "que el video siga disponible.")


def _info(url, options=None, action=None, download=False):
    """Run extract_info (and an optional follow-up inside the same client session)."""
    try:
        with _client({**BASE_OPTIONS, **(options or {})}) as ydl:
            data = ydl.extract_info(url, download=download)
            return action(ydl, data) if action else data
    except CaptureError:
        raise
    except Exception as exc:
        raise _failure(exc) from exc


def _check(data, identity, max_seconds):
    if data.get("_type") in ("playlist", "multi_video") or "entries" in data:
        raise CaptureError("Capture procesa un solo video de YouTube; no listas de reproducción.")
    if data.get("is_live") or data.get("live_status") in LIVE_STATES:
        raise CaptureError("No se procesan transmisiones en directo ni programadas; espere a que "
                           "quede grabada.")
    if data.get("availability") not in ALLOWED_AVAILABILITY or (data.get("age_limit") or 0) >= 18:
        raise CaptureError("El video de YouTube requiere iniciar sesión (privado, solo miembros o con "
                           "restricción de edad); Capture no usa cookies ni cuentas.")
    if data.get("id") != identity:
        raise CaptureError("YouTube devolvió un video distinto al solicitado.")
    duration = data.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        raise CaptureError("YouTube no informa la duración del video; no se procesa.")
    if duration > max_seconds:
        raise CaptureError("El video supera el máximo de 4 horas para Capture.")


def _manual_subtitle(data, language):
    """Only author-provided tracks (subtitles); automatic_captions is never read."""
    tracks = {key: value for key, value in (data.get("subtitles") or {}).items()
              if key != "live_chat" and isinstance(value, list)}
    language = (language or "").lower()
    candidates = [key for key in tracks if key.lower() == language] + sorted(
        key for key in tracks if key.lower().startswith(language + "-"))
    for key in candidates:
        for item in tracks[key]:
            if isinstance(item, dict) and item.get("ext") == "vtt" and item.get("url"):
                return key, item["url"]
    return None, None


def inspect(source, language, max_seconds=MAX_SECONDS):
    identity = video_id(source)
    url = "https://www.youtube.com/watch?v=" + identity
    data = _info(url)
    _check(data, identity, max_seconds)
    subtitle, _ = _manual_subtitle(data, language)
    upload = data.get("upload_date")
    if isinstance(upload, str) and re.fullmatch(r"\d{8}", upload):
        upload = f"{upload[:4]}-{upload[4:6]}-{upload[6:]}"
    title = data.get("title") or identity
    return {"kind": "youtube", "location": url, "original_name": title,
            "size_bytes": None, "mime_type": None, "sha256": None,
            "video_id": identity, "title": title, "channel": data.get("channel") or data.get("uploader"),
            "duration_seconds": data["duration"], "upload_date": upload, "url": url,
            "availability": data.get("availability"), "subtitle_language": subtitle,
            "text_source": "manual_subtitles" if subtitle else "audio_whisperx"}


def _seconds(value):
    parts = value.replace(",", ".").split(":")
    return round(sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts))), 3)


def parse_vtt(text, language=None):
    segments = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip().split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        match = re.match(r"\s*([\d:.,]+)\s*-->\s*([\d:.,]+)", lines[timing])
        if not match:
            continue
        body = " ".join(html.unescape(re.sub(r"<[^>]+>", "", line)) for line in lines[timing + 1:])
        body = " ".join(body.split())
        if body:
            segments.append({"start": _seconds(match.group(1)), "end": _seconds(match.group(2)),
                             "text": body})
    segments.sort(key=lambda item: item["start"])
    if not segments:
        raise CaptureError("Los subtítulos manuales de YouTube no contienen texto.")
    return {"language": language, "segments": segments}


def fetch_subtitles(metadata, target, expected=None):
    """Download the manual track again (its URL expires) and pin the converted JSON."""
    def download(ydl, data):
        _check(data, metadata["video_id"], MAX_SECONDS)
        key, url = _manual_subtitle(data, metadata["subtitle_language"])
        if key != metadata["subtitle_language"]:
            raise CaptureError("Cambiaron los subtítulos manuales de YouTube desde que se creó el job.")
        with ydl.urlopen(url) as response:
            return response.read()
    payload = _info(metadata["url"], action=download)
    part = Path(target).with_name(Path(target).name + ".part")
    atomic_json(part, parse_vtt(payload.decode("utf-8-sig"), metadata["subtitle_language"]))
    if expected and sha256(part) != expected:
        raise CaptureError("Los subtítulos manuales de YouTube cambiaron desde la primera descarga.")
    part.replace(target)
    return sha256(target)


def fetch(metadata, target, expected=None):
    """Download only bestaudio into the job's allowlisted temporary name."""
    part = Path(target).with_name(Path(target).name + ".part")
    part.unlink(missing_ok=True)
    options = {"format": "bestaudio", "outtmpl": {"default": str(part)}, "nopart": True,
               "overwrites": True, "continuedl": False, "fixup": "never", "postprocessors": []}
    # Re-check before downloading: never fetch a video that became live, private or longer.
    _check(_info(metadata["url"]), metadata["video_id"], MAX_SECONDS)
    data = _info(metadata["url"], options, download=True)
    _check(data, metadata["video_id"], MAX_SECONDS)
    if not part.is_file() or part.stat().st_size == 0:
        raise CaptureError("YouTube no entregó el audio del video.")
    if expected and sha256(part) != expected:
        raise CaptureError("El audio de YouTube cambió desde la primera descarga; no se acepta.")
    part.replace(target)
