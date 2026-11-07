#!/usr/bin/env python3
"""
MOTEUR PLAYOUT REELS 2026 — ÉDITION ANDROID TERMUX (100% PYTHON NATIF, ZÉRO COMPILATION)
- Publie depuis la VRAIE connexion mobile/Wi-Fi du téléphone Android (0% Shadowban Datacenter)
- Playout automatique 24h/24 : délai 6h par défaut OU publication immédiate dès (Moyenne + 20 vues)
- Compatible raccourci 1-clic Termux:Widget (~/.shortcuts/) + commande rapide `publier`
"""
import argparse
import base64
import gzip
import hashlib
import http.cookiejar
import json
import os
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

API_BASE = os.environ.get("REELS_API_BASE", "https://insta-reels-2026.vercel.app").rstrip("/")
HOME_DIR = Path.home() / ".reels2026"
SESSIONS_DIR = HOME_DIR / "sessions"
LOG_FILE = HOME_DIR / "playout.log"
PID_FILE = HOME_DIR / "daemon.pid"

HOME_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Vidéos test 0-1 vue publiées jadis depuis IP Azure US -> archivées automatiquement dès la 1re connexion
DATACENTER_LOW_VIEW_MEDIA_IDS = {"4024451941021066887", "4024583456837332212"}

# Empreintes matérielles Android fixes par compte (évite tout changement d'appareil suspect)
DEFAULT_ACCOUNT_UUIDS = {
    "sophiedigital.life": {
        "phone_id": "587ca3b9-973c-43c4-8271-bfae905a0eb0",
        "uuid": "5af780af-a459-4c76-94f7-7c0b75364b46",
        "client_session_id": "5b0bf42d-e97d-4eb4-9ecc-61fcb6498547",
        "advertising_id": "0f93b7b0-b311-46a4-aa6f-05fe3cb41463",
        "android_device_id": "android-efcc04b3cfbb67bb",
    },
    "xovan.vipx": {
        "phone_id": "c89a7f89-5622-46dd-aa6d-98f8fbb3d889",
        "uuid": "86e6d3de-0272-44b9-a35f-551b0b55c405",
        "client_session_id": "3ac65991-38ee-4159-a9c1-27cb5dbd381b",
        "advertising_id": "65efdff2-2fa7-46bb-8c28-37e7603e0d4c",
        "android_device_id": "android-5b8e72bacb93a97c",
    },
}

FALLBACK_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAx"
    "NDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF"
    "BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
    "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi"
    "4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APtQ/9k="
)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
            tail = LOG_FILE.read_text(encoding="utf-8", errors="ignore")[-200_000:]
            LOG_FILE.write_text(tail, encoding="utf-8")
        with open(LOG_FILE, "a", encoding="utf-8") as fp:
            fp.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def notify_android(title: str, content: str, url: str = "") -> None:
    if shutil.which("termux-toast"):
        try:
            subprocess.Popen(
                ["termux-toast", "-g", "top", f"{title}: {content}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    if shutil.which("termux-notification"):
        cmd = ["termux-notification", "--id", "reels2026", "--title", title, "--content", content]
        if url:
            cmd.extend(["--action", f"termux-open-url '{url}'"])
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def api_json(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "Reels2026-Termux/2.0"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def parse_utc(ts_str: str) -> datetime:
    s = (ts_str or "").strip().replace("T", " ").rstrip("Z")
    if "+" in s:
        s = s.split("+")[0].strip()
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime(2020, 1, 1, tzinfo=timezone.utc)


def download_drive_mp4(file_id: str, dest: Path) -> None:
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Android 14; Mobile)"})
    with opener.open(req, timeout=120) as resp, open(dest, "wb") as fp:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fp.write(chunk)
    if dest.stat().st_size < 10_000:
        raise RuntimeError(f"Fichier MP4 invalide ou trop petit ({dest.stat().st_size} octets)")


def parse_mp4_boxes(video_path: Path) -> tuple[int, int, float]:
    data = video_path.read_bytes()
    dur = 5.0
    w, h = 720, 1280
    containers = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta"}

    def walk(buf: bytes) -> None:
        nonlocal dur, w, h
        pos = 0
        n = len(buf)
        while pos + 8 <= n:
            size, btype = struct.unpack(">I4s", buf[pos : pos + 8])
            hdr = 8
            if size == 1 and pos + 16 <= n:
                size = struct.unpack(">Q", buf[pos + 8 : pos + 16])[0]
                hdr = 16
            elif size == 0:
                size = n - pos
            if size < hdr or pos + size > n:
                break
            body = buf[pos + hdr : pos + size]
            if btype in containers:
                walk(body)
            elif btype == b"mvhd" and len(body) >= 20:
                ver = body[0]
                if ver == 1 and len(body) >= 32:
                    ts, d = struct.unpack(">IQ", body[20:32])
                else:
                    ts, d = struct.unpack(">II", body[12:20])
                if ts > 0:
                    dur = d / ts
            elif btype == b"tkhd" and len(body) >= 84:
                ver = body[0]
                off = 88 if ver == 1 else 76
                if len(body) >= off + 8:
                    wf, hf = struct.unpack(">II", body[off : off + 8])
                    tw, th = wf >> 16, hf >> 16
                    if tw > 0 and th > 0:
                        w, h = tw, th
            pos += size

    walk(data)
    return w, h, max(1.0, dur)


def inspect_video_and_cover(video_path: Path, cover_path: Path) -> tuple[int, int, float]:
    w, h, dur = parse_mp4_boxes(video_path)
    if shutil.which("ffprobe"):
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(video_path)],
                timeout=20,
            )
            probe = json.loads(out.decode("utf-8"))
            vstreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
            if vstreams:
                vs = vstreams[0]
                vw = int(vs.get("width") or w)
                vh = int(vs.get("height") or h)
                rot = int((vs.get("tags") or {}).get("rotate") or 0)
                if abs(rot) in (90, 270):
                    vw, vh = vh, vw
                w, h = vw, vh
                dur = float(vs.get("duration") or (probe.get("format") or {}).get("duration") or dur)
        except Exception:
            pass

    cover_ok = False
    if shutil.which("ffmpeg"):
        try:
            seek = "0.2" if dur > 0.6 else "0.0"
            subprocess.check_call(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    seek,
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                    "-q:v",
                    "3",
                    str(cover_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            cover_ok = cover_path.exists() and cover_path.stat().st_size > 500
        except Exception:
            cover_ok = False

    if not cover_ok:
        cover_path.write_bytes(base64.b64decode(FALLBACK_JPEG_B64))

    return w, h, max(1.0, dur)


class IgLoginExpired(Exception):
    pass


class AndroidIGClient:
    """Client Instagram Android v449 100% Python Standard Library (0 dépendance compilée)."""

    API_DOMAIN = "i.instagram.com"
    APP_ID = "567067343352427"
    BLOKS_VERSION = "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
    USER_AGENT = (
        "Instagram 449.0.0.52.84 Android (34/14; 480dpi; 1344x2992; "
        "Google/google; Pixel 8 Pro; husky; husky; fr_FR; 1079242191)"
    )
    DEVICE_DICT = {
        "manufacturer": "Google/google",
        "model": "Pixel 8 Pro",
        "android_version": 34,
        "android_release": "14",
    }

    def __init__(self, username: str, sessionid: str, uuids: dict | None = None, mid: str = "", ig_u_rur: str = ""):
        self.username = username
        self.sessionid = urllib.parse.unquote(sessionid.strip())
        m = re.match(r"^(\d+):", self.sessionid)
        if not m:
            raise IgLoginExpired("Format sessionid invalide")
        self.user_id = m.group(1)

        base_u = DEFAULT_ACCOUNT_UUIDS.get(username) or {}
        merged_u = {**base_u, **(uuids or {})}
        self.phone_id = merged_u.get("phone_id") or str(uuid.uuid4())
        self.uuid = merged_u.get("uuid") or str(uuid.uuid4())
        self.client_session_id = merged_u.get("client_session_id") or str(uuid.uuid4())
        self.advertising_id = merged_u.get("advertising_id") or str(uuid.uuid4())
        self.android_device_id = merged_u.get("android_device_id") or (
            "android-" + hashlib.sha256(self.uuid.encode()).hexdigest()[:16]
        )
        self.pigeon_session_id = f"UFS-{uuid.uuid4()}-1"
        self.mid = mid or ""
        self.ig_u_rur = ig_u_rur or ""
        self.timezone_offset = 3600  # Africa/Porto-Novo (UTC+1)

    def authorization_header(self) -> str:
        auth_obj = {
            "ds_user_id": str(self.user_id),
            "sessionid": self.sessionid,
            "should_use_header_over_cookies": True,
        }
        b64 = base64.b64encode(json.dumps(auth_obj, separators=(",", ":")).encode("utf-8")).decode("ascii")
        return f"Bearer IGT:2:{b64}"

    def build_headers(self, extra: dict | None = None) -> dict:
        h = {
            "X-IG-App-Locale": "fr_FR",
            "X-IG-Device-Locale": "fr_FR",
            "X-IG-Mapped-Locale": "fr_FR",
            "X-Pigeon-Session-Id": self.pigeon_session_id,
            "X-Pigeon-Rawclienttime": f"{time.time():.3f}",
            "X-IG-Bandwidth-Speed-KBPS": "-1.000",
            "X-IG-Bandwidth-TotalBytes-B": "0",
            "X-IG-Bandwidth-TotalTime-MS": "0",
            "X-IG-App-Startup-Country": "BJ",
            "X-Bloks-Version-Id": self.BLOKS_VERSION,
            "X-IG-WWW-Claim": "0",
            "X-Bloks-Is-Layout-RTL": "false",
            "X-Bloks-Is-Panorama-Enabled": "true",
            "X-IG-Device-ID": self.uuid,
            "X-IG-Family-Device-ID": self.phone_id,
            "X-IG-Android-ID": self.android_device_id,
            "X-IG-Timezone-Offset": str(self.timezone_offset),
            "X-IG-Connection-Type": "WIFI",
            "X-IG-Capabilities": "3brTv10=",
            "X-IG-App-ID": self.APP_ID,
            "Priority": "u=3",
            "User-Agent": self.USER_AGENT,
            "Accept-Language": "fr-FR, en-US",
            "Accept-Encoding": "gzip",
            "X-FB-HTTP-Engine": "Tigon/MNS/TCP",
            "X-Tigon-Is-Retry": "False",
            "Authorization": self.authorization_header(),
        }
        if self.mid:
            h["X-MID"] = self.mid
        if self.ig_u_rur:
            h["IG-U-RUR"] = self.ig_u_rur
        if extra:
            h.update(extra)
        return h

    def export_session_dict(self) -> dict:
        return {
            "username": self.username,
            "ds_user_id": str(self.user_id),
            "sessionid": self.sessionid,
            "mid": self.mid,
            "ig_u_rur": self.ig_u_rur,
            "uuids": {
                "phone_id": self.phone_id,
                "uuid": self.uuid,
                "client_session_id": self.client_session_id,
                "advertising_id": self.advertising_id,
                "android_device_id": self.android_device_id,
            },
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def raw_request(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict | None = None,
        timeout: int = 60,
    ) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(url, data=body, headers=self.build_headers(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers.items())
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = dict(e.headers.items()) if e.headers else {}
            raw = e.read() if e.fp else b""

        if resp_headers.get("Content-Encoding", "").lower() == "gzip" or resp_headers.get("content-encoding", "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass

        for k, v in resp_headers.items():
            kl = k.lower()
            if kl == "ig-set-x-mid" and v:
                self.mid = v
            elif kl == "ig-set-ig-u-rur" and v:
                self.ig_u_rur = v

        return status, raw, resp_headers

    def api_request(
        self,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
        signed: bool = True,
    ) -> tuple[int, dict]:
        time.sleep(random.uniform(0.8, 1.8))
        ep = endpoint if endpoint.startswith("/") else f"/v1/{endpoint}"
        url = f"https://{self.API_DOMAIN}/api{ep}"
        if params:
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if qs:
                url = f"{url}{'&' if '?' in url else '?'}{qs}"

        if data is not None:
            if signed:
                dumped = json.dumps(data, separators=(",", ":"))
                encoded_body = f"signed_body=SIGNATURE.{urllib.parse.quote_plus(dumped)}".encode("utf-8")
            else:
                encoded_body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
            status, raw, _ = self.raw_request(url, method="POST", body=encoded_body, headers=hdrs, timeout=45)
        else:
            status, raw, _ = self.raw_request(url, method="GET", timeout=45)

        txt = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(txt)
        except Exception:
            parsed = {"raw_text": txt[:400]}

        msg = str(parsed.get("message") or "")
        if status in (401, 403) and ("login_required" in msg or "cookie" in msg.lower() or parsed.get("logout_reason")):
            raise IgLoginExpired(f"Session expirée ({msg or status})")
        if msg == "login_required":
            raise IgLoginExpired("login_required — cookie expiré")

        return status, parsed

    def verify_session(self) -> str:
        status, res = self.api_request(f"users/{self.user_id}/info/")
        if status == 200 and res.get("user"):
            uname = res["user"].get("username") or self.username
            self.username = uname
            return uname
        status2, res2 = self.api_request("accounts/current_user/?edit=true")
        if status2 == 200 and res2.get("user"):
            uname = res2["user"].get("username") or self.username
            self.username = uname
            return uname
        raise RuntimeError(f"Impossible de vérifier @{self.username}: HTTP {status} {res}")

    def fetch_recent_medias(self) -> list[dict]:
        by_pk: dict[str, dict] = {}

        # 1. Endpoint Clips/Reels (contient play_count exact des Reels)
        try:
            st, res = self.api_request(
                "clips/user/",
                data={
                    "target_user_id": str(self.user_id),
                    "max_id": "",
                    "page_size": "30",
                    "include_feed_video": "true",
                },
                signed=False,
            )
            if st == 200:
                for item in res.get("items") or []:
                    m = item.get("media") if isinstance(item, dict) and "media" in item else item
                    if isinstance(m, dict) and m.get("pk"):
                        pk = str(m["pk"])
                        plays = max(
                            int(m.get("play_count") or 0),
                            int(m.get("ig_play_count") or 0),
                            int(m.get("view_count") or 0),
                            int(m.get("video_play_count") or 0),
                        )
                        by_pk[pk] = {
                            "pk": pk,
                            "code": m.get("code") or "",
                            "play_count": plays,
                            "like_count": int(m.get("like_count") or 0),
                            "comment_count": int(m.get("comment_count") or 0),
                            "taken_at": int(m.get("taken_at") or 0),
                        }
        except IgLoginExpired:
            raise
        except Exception:
            pass

        # 2. Endpoint Feed utilisateur en complément
        try:
            st2, res2 = self.api_request(
                f"feed/user/{self.user_id}/",
                params={"count": "25", "ranked_content": "true"},
            )
            if st2 == 200:
                for m in res2.get("items") or []:
                    if isinstance(m, dict) and m.get("pk"):
                        pk = str(m["pk"])
                        plays = max(
                            int(m.get("play_count") or 0),
                            int(m.get("ig_play_count") or 0),
                            int(m.get("view_count") or 0),
                            int(m.get("video_play_count") or 0),
                            by_pk.get(pk, {}).get("play_count", 0),
                        )
                        by_pk[pk] = {
                            "pk": pk,
                            "code": m.get("code") or by_pk.get(pk, {}).get("code", ""),
                            "play_count": plays,
                            "like_count": max(int(m.get("like_count") or 0), by_pk.get(pk, {}).get("like_count", 0)),
                            "comment_count": max(
                                int(m.get("comment_count") or 0), by_pk.get(pk, {}).get("comment_count", 0)
                            ),
                            "taken_at": int(m.get("taken_at") or by_pk.get(pk, {}).get("taken_at", 0)),
                        }
        except IgLoginExpired:
            raise
        except Exception:
            pass

        return sorted(by_pk.values(), key=lambda x: x.get("taken_at", 0), reverse=True)

    def archive_media(self, media_pk: str) -> bool:
        full_id = f"{media_pk}_{self.user_id}" if "_" not in str(media_pk) else str(media_pk)
        payload = {
            "_uuid": self.uuid,
            "device_id": self.android_device_id,
            "radio_type": "wifi-none",
            "media_id": full_id,
        }
        st, res = self.api_request(f"media/{full_id}/only_me/", data=payload, signed=True)
        return st == 200 and res.get("status") == "ok"

    def upload_reel(self, video_path: Path, caption: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            cover_path = Path(tmp) / "cover.jpg"
            width, height, duration = inspect_video_and_cover(video_path, cover_path)
            clip_bytes = video_path.read_bytes()
            clip_len = str(len(clip_bytes))
            cover_bytes = cover_path.read_bytes()
            cover_len = str(len(cover_bytes))

        upload_id = str(int(time.time() * 1000))
        duration_ms = str(int(round(duration * 1000)))
        composer_session_id = str(uuid.uuid4())
        asset_id = uuid.uuid4().hex[:12].upper()
        upload_name = f"{uuid.uuid4().hex}-0-{clip_len}-{upload_id}-{upload_id}"

        # 1. Upload settings
        upload_settings = {
            "composer_session_id": composer_session_id,
            "upload_setting_properties": {
                "upload_settings_version": "v0.1",
                "codec": {},
                "context": {
                    "source_attribution": None,
                    "enable_video_dimension_upscale": False,
                    "source_type": "clips",
                    "quality": "",
                    "target_id": int(self.user_id),
                },
                "video": {
                    "video_height": height,
                    "video_gop_size_sec": 0,
                    "video_rotation_angle": 0,
                    "video_width": width,
                    "source_video_codec": None,
                    "video_partial_frame_size_bytes": 0,
                    "asset_id": asset_id,
                    "video_key_frame_size_bytes": 0,
                    "target_duration": max(1, int(round(duration))),
                    "video_original_file_size": int(clip_len),
                    "video_duration_milliseconds": int(duration_ms),
                    "audio_bit_rate_bps": -1,
                    "video_bit_rate_bps": 0,
                    "audio_codec_type": None,
                    "video_fps": 30,
                },
                "creative_tools": {"transmuxing_eligible": False, "transcoding_required": True},
                "network": {
                    "download_latency_connection_quality": "ig_dummy",
                    "network_connection_name": "ig_dummy",
                    "download_bandwidth_connection_quality": "ig_dummy",
                },
            },
            "preview_spec": {
                "spec_version": 1,
                "video_dur_ms": int(duration_ms),
                "audio_dur_ms": int(duration_ms),
            },
        }
        us_bytes = json.dumps(upload_settings, separators=(",", ":")).encode("utf-8")
        us_len = str(len(us_bytes))
        self.raw_request(
            f"https://{self.API_DOMAIN}/upload_settings/{composer_session_id}",
            method="POST",
            body=us_bytes,
            headers={
                "Content-Type": "application/json",
                "Content-Length": us_len,
                "Offset": "0",
                "X-Entity-Length": us_len,
                "X-Entity-Name": "upload_settings",
                "X-Entity-Type": "application/json",
                "X_FB_VIDEO_WATERFALL_ID": f"{composer_session_id}_settings",
            },
            timeout=30,
        )

        # 2. Rupload Video Init + Binary Upload
        rupload_params = {
            "provenance_metadata": json.dumps({"origin": ["EXTERNAL"]}),
            "upload_media_height": str(height),
            "share_type": "reels",
            "debug_segment_id": "0",
            "extract_cover_frame": "1",
            "upload_engine_config_enum": "0",
            "xsharing_user_ids": "[]",
            "upload_media_width": str(width),
            "stella_data": "{}",
            "is_clips_video": "1",
            "is_optimistic_upload": "true",
            "upload_media_duration_ms": duration_ms,
            "content_tags": "use_default_cover",
            "upload_id": upload_id,
            "retry_context": '{"num_reupload":0,"num_step_manual_retry":0,"num_step_auto_retry":0}',
            "session_id": upload_id,
            "media_type": "2",
        }
        vid_headers = {
            "X-Instagram-Rupload-Params": json.dumps(rupload_params, separators=(",", ":")),
            "X_FB_VIDEO_WATERFALL_ID": f"{composer_session_id}_{asset_id}_Mixed_0",
            "X-Entity-Type": "video/mp4",
            "Segment-Start-Offset": "0",
            "Segment-Type": "3",
        }
        st_init, raw_init, _ = self.raw_request(
            f"https://{self.API_DOMAIN}/rupload_igvideo/{upload_name}",
            method="GET",
            headers=vid_headers,
            timeout=30,
        )
        if st_init != 200:
            raise RuntimeError(f"Échec init rupload_igvideo ({st_init}): {raw_init.decode('utf-8', 'ignore')[:200]}")

        st_up, raw_up, _ = self.raw_request(
            f"https://{self.API_DOMAIN}/rupload_igvideo/{upload_name}",
            method="POST",
            body=clip_bytes,
            headers={
                **vid_headers,
                "Offset": "0",
                "X-Entity-Name": upload_name,
                "X-Entity-Length": clip_len,
                "Content-Type": "application/octet-stream",
                "Content-Length": clip_len,
            },
            timeout=180,
        )
        if st_up != 200:
            raise RuntimeError(f"Échec envoi MP4 rupload_igvideo ({st_up}): {raw_up.decode('utf-8', 'ignore')[:200]}")

        # 3. Rupload Cover JPEG
        photo_name = f"{upload_id}_0_{random.randint(1000000000, 9999999999)}"
        photo_params = {
            "retry_context": '{"num_step_auto_retry":0,"num_reupload":0,"num_step_manual_retry":0}',
            "media_type": "1",
            "xsharing_user_ids": "[]",
            "upload_id": upload_id,
            "image_compression": json.dumps({"lib_name": "moz", "lib_version": "3.1.m", "quality": "80"}),
        }
        st_ph, raw_ph, _ = self.raw_request(
            f"https://{self.API_DOMAIN}/rupload_igphoto/{photo_name}",
            method="POST",
            body=cover_bytes,
            headers={
                "X-Instagram-Rupload-Params": json.dumps(photo_params, separators=(",", ":")),
                "X_FB_PHOTO_WATERFALL_ID": str(uuid.uuid4()),
                "X-Entity-Type": "image/jpeg",
                "Offset": "0",
                "X-Entity-Name": photo_name,
                "X-Entity-Length": cover_len,
                "Content-Type": "application/octet-stream",
                "Content-Length": cover_len,
            },
            timeout=60,
        )
        if st_ph != 200:
            log(f"[@{self.username}] Note miniature ({st_ph}) — couverture auto Instagram activée.")

        # 4. Configure to Clips (avec attente transcodage serveur Instagram)
        cfg_data = {
            "_uuid": self.uuid,
            "device_id": self.android_device_id,
            "filter_type": "0",
            "timezone_offset": str(self.timezone_offset),
            "location": "{}",
            "source_type": "4",
            "caption": caption,
            "usertags": '{"in":[]}',
            "clips_share_preview_to_feed": "1",
            "upload_id": upload_id,
            "device": self.DEVICE_DICT,
            "length": round(duration, 2),
            "clips_segments_metadata": {
                "num_segments": 1,
                "clips_segments": [
                    {
                        "index": 0,
                        "face_effect_id": None,
                        "speed": 100,
                        "source_type": 4,
                        "duration_ms": int(duration_ms),
                        "audio_type": "original",
                        "from_draft": 0,
                        "camera_position": -1,
                        "media_folder": None,
                        "media_type": "video",
                        "original_media_type": 2,
                        "original_segment_hash": None,
                        "is_remix": False,
                        "has_reusable_template_asset": False,
                    }
                ],
            },
            "clips_audio_metadata": {"original": {"volume_level": 1.0}},
            "additional_audio_info": {"has_voiceover_attribution": 0},
            "edits": {"filter_type": 0, "filter_strength": 1.0},
            "capture_type": "clips_v2",
            "clips_creation_entry_point": "clips",
            "camera_entry_point": "71",
            "audience": "default",
            "include_e2ee_mentioned_user_list": "1",
            "hide_from_profile_grid": "false",
            "original_width": width,
            "original_height": height,
            "extra": {"source_width": width, "source_height": height},
            "audio_muted": False,
            "poster_frame_index": 70,
        }

        for attempt in range(1, 18):
            wait_s = 7 if attempt == 1 else 8
            time.sleep(wait_s)
            st_cfg, res_cfg = self.api_request("media/configure_to_clips/?video=1", data=cfg_data, signed=True)
            if st_cfg == 200 and res_cfg.get("status") == "ok" and res_cfg.get("media"):
                return res_cfg["media"]
            msg = str(res_cfg.get("message") or "")
            if st_cfg == 202 or "Transcode not finished" in msg or "transcode" in msg.lower():
                log(f"[@{self.username}] ⏳ Encodage Instagram en cours (essai {attempt}/17)...")
                continue
            raise RuntimeError(f"Échec configure_to_clips (HTTP {st_cfg}): {res_cfg}")

        raise RuntimeError("Délai dépassé lors du transcodage Instagram.")


def extract_session_from_account(uname: str, acc_row: dict) -> AndroidIGClient | None:
    local_path = SESSIONS_DIR / f"{uname}.json"
    local_data = {}
    if local_path.exists():
        try:
            local_data = json.loads(local_path.read_text(encoding="utf-8"))
        except Exception:
            local_data = {}

    turso_data = {}
    raw_turso = acc_row.get("session_json") or ""
    if raw_turso:
        try:
            turso_data = json.loads(raw_turso)
        except Exception:
            turso_data = {}

    # Extraire sessionid Turso (prioritaire s'il vient d'être mis à jour via Kiwi Browser !)
    turso_sid = urllib.parse.unquote(
        str(
            turso_data.get("sessionid")
            or ((turso_data.get("instagrapi_settings") or {}).get("authorization_data") or {}).get("sessionid")
            or ""
        ).strip()
    )
    local_sid = urllib.parse.unquote(str(local_data.get("sessionid") or "").strip())

    chosen = None
    if acc_row.get("status") == "expired":
        return None

    if turso_sid and turso_sid != local_sid:
        uuids = (
            turso_data.get("uuids")
            or (turso_data.get("instagrapi_settings") or {}).get("uuids")
            or local_data.get("uuids")
        )
        chosen = {
            "sessionid": turso_sid,
            "uuids": uuids,
            "mid": turso_data.get("mid") or local_data.get("mid") or "",
            "ig_u_rur": turso_data.get("ig_u_rur") or local_data.get("ig_u_rur") or "",
        }
    elif local_sid:
        chosen = local_data
    elif turso_sid:
        uuids = turso_data.get("uuids") or (turso_data.get("instagrapi_settings") or {}).get("uuids")
        chosen = {
            "sessionid": turso_sid,
            "uuids": uuids,
            "mid": turso_data.get("mid") or "",
            "ig_u_rur": turso_data.get("ig_u_rur") or "",
        }

    if not chosen or not chosen.get("sessionid"):
        return None

    client = AndroidIGClient(
        username=uname,
        sessionid=chosen["sessionid"],
        uuids=chosen.get("uuids"),
        mid=chosen.get("mid") or "",
        ig_u_rur=chosen.get("ig_u_rur") or "",
    )
    client.verify_session()
    local_path.write_text(json.dumps(client.export_session_dict(), indent=2), encoding="utf-8")
    return client


def run_playout_cycle(force_now: bool = False, target_account: str = "") -> int:
    state = api_json("/api/worker?client=Termux-Android")
    if not state.get("ok"):
        log(f"⚠️ Réponse API inattendue: {state}")
        return 0

    settings = state.get("settings") or {}
    if settings.get("enabled") == "false" and not force_now:
        log("⏸️ Playout en pause sur le Dashboard.")
        return 0

    interval_h = float(settings.get("interval_hours") or 6.0)
    bonus_views = int(settings.get("viral_bonus_views") or 20)
    dist_mode = settings.get("distribution_mode") or "split"
    all_logs = state.get("published_logs") or []

    remote_force_raw = (settings.get("termux_force_publish") or "").strip()
    force_token_file = HOME_DIR / "last_force_token.txt"
    prev_token = force_token_file.read_text().strip() if force_token_file.exists() else ""
    if remote_force_raw and remote_force_raw != prev_token:
        force_token_file.write_text(remote_force_raw, encoding="utf-8")
        rf_kind = remote_force_raw.split(":")[0].strip()
        if rf_kind in ("all", "both"):
            force_now = True
        elif rf_kind in ("sophiedigital.life", "xovan.vipx"):
            force_now = True
            target_account = rf_kind

    published_count = 0
    connected_any = False

    for acc in state.get("accounts") or []:
        uname = acc["username"]
        if target_account and target_account not in ("all", "both"):
            t_norm = target_account.lower().strip()
            if t_norm not in uname.lower():
                continue

        try:
            cl = extract_session_from_account(uname, acc)
        except IgLoginExpired as e:
            log(f"[@{uname}] 🔴 Cookie expiré ({e}) — envoyez le nouveau cookie Kiwi Browser pour reconnecter.")
            try:
                api_json("/api/accounts", {"username": uname, "status": "expired", "auto_dispatch": False})
            except Exception:
                pass
            notify_android(f"Cookie expiré @{uname}", "Envoyez votre cookie Kiwi Browser pour relancer.")
            continue
        except Exception as e:
            log(f"[@{uname}] ⚠️ Erreur connexion temporaire: {e}")
            continue

        if not cl:
            log(f"[@{uname}] ⏳ En attente de reconnexion du cookie Kiwi Browser...")
            continue

        connected_any = True
        medias = cl.fetch_recent_medias()

        # 1. Archiver automatiquement les 2 anciens Reels datacenter à 0-1 vue
        archived_ids = []
        for m in medias:
            mpk = str(m["pk"])
            plays = int(m.get("play_count") or 0)
            if mpk in DATACENTER_LOW_VIEW_MEDIA_IDS and plays <= 5:
                try:
                    if cl.archive_media(mpk):
                        archived_ids.append(mpk)
                        log(f"[@{uname}] 🧹 Ancienne vidéo datacenter ({plays} vue) archivée !")
                except Exception:
                    pass

        by_pk = {str(m["pk"]): m for m in medias if str(m["pk"]) not in archived_ids}
        acc_logs = [
            x
            for x in all_logs
            if x["account_username"] == uname and str(x.get("ig_media_id")) not in DATACENTER_LOW_VIEW_MEDIA_IDS
        ]

        log_updates = []
        for row in acc_logs:
            m = by_pk.get(str(row.get("ig_media_id")))
            if m:
                v = int(m.get("play_count") or 0)
                lk = int(m.get("like_count") or 0)
                cm = int(m.get("comment_count") or 0)
                row["views_count"] = v
                log_updates.append({"id": row["id"], "views": v, "likes": lk, "comments": cm})

        organic_plays = [
            int(m.get("play_count") or 0) for m in medias if str(m["pk"]) not in DATACENTER_LOW_VIEW_MEDIA_IDS
        ]

        if not acc_logs:
            avg_prev = (sum(organic_plays) / len(organic_plays)) if organic_plays else 0.0
            overall_avg = avg_prev
            latest_views = organic_plays[0] if organic_plays else 0
            threshold = int(round(avg_prev)) + bonus_views
            should_publish = True
            reason = f"🚀 1re sortie Playout Termux Mobile (moy. organique {overall_avg:.0f} vues)"
        else:
            latest = acc_logs[-1]
            latest_ig_id = str(latest.get("ig_media_id") or "")
            latest_media = by_pk.get(latest_ig_id)
            latest_views = int(latest_media.get("play_count") if latest_media else (latest.get("views_count") or 0))

            prior_plays = [
                int(m.get("play_count") or 0)
                for m in medias
                if str(m["pk"]) not in DATACENTER_LOW_VIEW_MEDIA_IDS and str(m["pk"]) != latest_ig_id
            ]
            if prior_plays:
                avg_prev = sum(prior_plays) / len(prior_plays)
            elif len(acc_logs) > 1:
                avg_prev = sum(int(x.get("views_count") or 0) for x in acc_logs[:-1]) / (len(acc_logs) - 1)
            else:
                avg_prev = 0.0

            elapsed_h = (datetime.now(timezone.utc) - parse_utc(latest.get("published_at") or "")).total_seconds() / 3600.0
            if organic_plays and (latest_views > 0 or elapsed_h >= 1.0):
                overall_avg = sum(organic_plays) / len(organic_plays)
            else:
                overall_avg = avg_prev

            threshold = int(round(avg_prev)) + bonus_views

            if force_now:
                should_publish = True
                reason = f"⚡ Raccourci Direct Termux (Moyenne {overall_avg:.0f} vues)"
            elif elapsed_h >= 0.15 and latest_views >= threshold:
                should_publish = True
                reason = f"🔥 Seuil viral atteint en {elapsed_h:.1f}h ({latest_views} ≥ {threshold} vues [moy. {avg_prev:.0f}+20])"
            elif elapsed_h >= interval_h:
                should_publish = True
                reason = f"⏱️ Délai {interval_h:g}h atteint ({latest_views} vues)"
            else:
                should_publish = False
                reason = ""
                rem_h = max(0.0, interval_h - elapsed_h)
                log(
                    f"[@{uname}] 👁️ Dernier: {latest_views} vues | Moyenne: {overall_avg:.1f} | "
                    f"Sortie anticipée dès {threshold} vues OU dans {rem_h:.1f}h"
                )

        session_export = cl.export_session_dict()
        (SESSIONS_DIR / f"{uname}.json").write_text(json.dumps(session_export, indent=2), encoding="utf-8")

        api_json(
            "/api/worker",
            {
                "action": "sync_metrics",
                "log_updates": log_updates,
                "archived_media_ids": archived_ids,
                "account_state": {
                    "username": uname,
                    "avg_views": round(overall_avg, 1),
                    "latest_views": latest_views,
                    "next_threshold": threshold,
                    "session_json": session_export,
                },
            },
        )

        if not should_publish:
            continue

        nxt = api_json(
            "/api/worker",
            {"action": "next_reel", "username": uname, "distribution_mode": dist_mode},
        ).get("reel")

        if not nxt:
            log(f"[@{uname}] 🎉 Tous les Reels du catalogue ont été publiés !")
            continue

        log(f"[@{uname}] 🎬 Téléchargement & publication : {nxt['filename']}")
        log(f"[@{uname}] 💬 Légende : {nxt['full_caption']}")

        with tempfile.TemporaryDirectory() as tmp:
            vpath = Path(tmp) / nxt["filename"]
            download_drive_mp4(nxt["drive_file_id"], vpath)
            media = cl.upload_reel(vpath, nxt["full_caption"])
            code = media.get("code") or ""
            media_pk = str(media.get("pk") or media.get("id") or "").split("_")[0]
            permalink = f"https://www.instagram.com/reel/{code}/" if code else ""
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            # Recalcul immédiat du prochain seuil (nouvelle moyenne incluant la série en cours + 20)
            new_threshold = int(round(overall_avg)) + bonus_views
            session_export = cl.export_session_dict()
            (SESSIONS_DIR / f"{uname}.json").write_text(json.dumps(session_export, indent=2), encoding="utf-8")

            api_json(
                "/api/worker",
                {
                    "action": "record_publish",
                    "reel_id": nxt["id"],
                    "drive_file_id": nxt["drive_file_id"],
                    "filename": nxt["filename"],
                    "username": uname,
                    "scheduled_slot": "Termux-Mobile",
                    "scheduled_date": datetime.now().strftime("%Y-%m-%d"),
                    "ig_media_id": media_pk,
                    "permalink": permalink,
                    "avg_before": round(overall_avg, 1),
                    "next_threshold": new_threshold,
                    "trigger_reason": reason,
                    "published_at_utc": now_utc,
                    "session_json": session_export,
                },
            )
            published_count += 1
            log(f"[@{uname}] ✅ REEL PUBLIÉ DEPUIS VOTRE IP MOBILE : {permalink}")
            notify_android(f"🎬 Reel publié (@{uname})", nxt["full_caption"][:60], permalink)

    if not connected_any:
        log("ℹ️ Aucun compte actif pour l'instant : envoyez vos cookies Kiwi Browser pour activer la publication.")

    return published_count


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_daemon() -> None:
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if old_pid != os.getpid() and is_pid_running(old_pid):
                print(f"🟢 Playout Termux déjà actif en arrière-plan (PID {old_pid}).")
                return
        except Exception:
            pass

    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    def cleanup(_sig=None, _frame=None):
        try:
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    if shutil.which("termux-wake-lock"):
        try:
            subprocess.Popen(["termux-wake-lock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    log("==========================================================")
    log("📱 PLAYOUT REELS 24/7 DÉMARRÉ SUR ANDROID TERMUX")
    log("Règle : 6h entre 2 vidéos OU immédiat dès (Moyenne + 20 vues)")
    log("==========================================================")

    last_full_check = 0.0
    last_seen_Status: dict[str, str] = {}

    while True:
        now = time.time()
        trigger_full = (now - last_full_check) >= 20 * 60
        force_target = ""
        force_flag = False

        # Heartbeat léger toutes les 60s vers Vercel (0 appel vers Instagram !)
        try:
            hb = api_json("/api/worker?mode=heartbeat&client=Termux-Daemon", timeout=20)
            fp = (hb.get("force_publish") or (hb.get("settings") or {}).get("termux_force_publish") or "").strip()
            force_token_file = HOME_DIR / "last_force_token.txt"
            prev_token = force_token_file.read_text().strip() if force_token_file.exists() else ""
            if fp and fp != prev_token:
                trigger_full = True
                rf_kind = fp.split(":")[0].strip()
                if rf_kind != "check":
                    force_flag = True
                    force_target = rf_kind
            for a in hb.get("accounts") or []:
                u = a["username"]
                sig = f"{a.get('status')}:{a.get('updated_at')}"
                if a.get("status") == "connected" and last_seen_Status.get(u) != sig:
                    log(f"[@{u}] 🔄 Cookie actif détecté sur le serveur — synchronisation immédiate !")
                    trigger_full = True
                last_seen_Status[u] = sig
        except Exception:
            pass

        if trigger_full:
            try:
                run_playout_cycle(force_now=force_flag, target_account=force_target)
                last_full_check = time.time()
            except Exception as e:
                log(f"⚠️ Réseau mobile indisponible ou erreur passagère ({e}) — nouvel essai auto.")

        time.sleep(60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Playout Instagram Reels 2026 — Termux Android")
    parser.add_argument("--force-now", action="store_true", help="Publier immédiatement le prochain Reel")
    parser.add_argument("--daemon", action="store_true", help="Lancer le Playout automatique 24h/24 en arrière-plan")
    parser.add_argument("--once", action="store_true", help="Vérifier les vues maintenant et appliquer 6h / Moyenne+20")
    parser.add_argument("--account", default="", help="Cibler un seul compte (sophie ou xovan)")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    elif args.force_now:
        log("⚡ RACCOURCI PUBLICATION DIRECTE LANCÉ...")
        run_playout_cycle(force_now=True, target_account=args.account)
    else:
        run_playout_cycle(force_now=False, target_account=args.account)


if __name__ == "__main__":
    main()
