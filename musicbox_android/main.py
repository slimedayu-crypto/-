"""
MusicBox Pro — Android Edition (Full)
Полный перенос функциональности desktop-версии (tkinter) на Kivy/Android.

Запуск на ПК для отладки:
    pip install kivy yt-dlp mutagen requests plyer
    python main.py
"""

import os
import json
import threading
import sqlite3
import time
import hashlib
import urllib.parse
from pathlib import Path

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import Clock, mainthread
from kivy.core.audio import SoundLoader
from kivy.core.window import Window
from kivy.uix.screenmanager import Screen
from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.properties import StringProperty, NumericProperty, BooleanProperty, ListProperty
from kivy.utils import platform, get_color_from_hex

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3
    from mutagen.flac import FLAC
    from mutagen import File as MutagenFile
    MUTAGEN_OK = True
except Exception:
    MUTAGEN_OK = False

try:
    import yt_dlp
    YTDLP_OK = True
except Exception:
    YTDLP_OK = False

try:
    import requests
    REQUESTS_OK = True
except Exception:
    REQUESTS_OK = False

# ───────────────────────── Android permissions / storage ─────────────────────────
if platform == "android":
    from android.permissions import request_permissions, Permission
    from android.storage import primary_external_storage_path
    request_permissions([
        Permission.READ_EXTERNAL_STORAGE,
        Permission.WRITE_EXTERNAL_STORAGE,
        Permission.INTERNET,
    ])
    MUSIC_DIR = os.path.join(primary_external_storage_path(), "Music", "MusicBoxPro")
else:
    MUSIC_DIR = os.path.join(str(Path.home()), "MusicBoxPro")

COVERS_DIR = os.path.join(MUSIC_DIR, "_covers")
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(COVERS_DIR, exist_ok=True)
DB_PATH = os.path.join(MUSIC_DIR, "library.db")
SETTINGS_PATH = os.path.join(MUSIC_DIR, "settings.json")

# ───────────────────────── Темы ─────────────────────────
THEMES = {
    "Dark":   {"bg": "#121218", "panel": "#1e1e28", "accent": "#3b82f6", "text": "#ffffff", "sub": "#9090a0"},
    "Purple": {"bg": "#15101f", "panel": "#241b35", "accent": "#a855f7", "text": "#ffffff", "sub": "#a698b8"},
    "Green":  {"bg": "#0f1712", "panel": "#1a261d", "accent": "#22c55e", "text": "#ffffff", "sub": "#8fa896"},
    "Sunset": {"bg": "#1a1010", "panel": "#2b1a18", "accent": "#f97316", "text": "#ffffff", "sub": "#b89a90"},
}


def load_settings():
    default = {"theme": "Dark", "volume": 0.8}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                default.update(json.load(f))
        except Exception:
            pass
    return default


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ───────────────────────── База данных ─────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS tracks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT UNIQUE, title TEXT, artist TEXT, album TEXT, year TEXT, genre TEXT,
        duration REAL, cover TEXT, lyrics TEXT, file_hash TEXT, liked INTEGER DEFAULT 0,
        added REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS playlists(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS playlist_tracks(
        playlist_id INTEGER, track_id INTEGER, position INTEGER,
        PRIMARY KEY(playlist_id, track_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS history(
        id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, played_at REAL)""")
    return conn


def file_hash(path, chunk=65536):
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(chunk))
        h.update(str(os.path.getsize(path)).encode())
        return h.hexdigest()
    except Exception:
        return None


def read_tags(path):
    info = {
        "path": path, "title": Path(path).stem, "artist": "", "album": "",
        "year": "", "genre": "", "duration": 0.0, "cover": None,
        "file_hash": file_hash(path),
    }
    if not MUTAGEN_OK:
        return info
    try:
        ext = Path(path).suffix.lower()
        if ext == ".mp3":
            audio = MP3(path)
            info["duration"] = audio.info.length
            try:
                tags = ID3(path)
                info["title"] = str(tags.get("TIT2", info["title"]))
                info["artist"] = str(tags.get("TPE1", ""))
                info["album"] = str(tags.get("TALB", ""))
                info["year"] = str(tags.get("TDRC", ""))
                info["genre"] = str(tags.get("TCON", ""))
                for tag in tags.values():
                    if hasattr(tag, "FrameID") and tag.FrameID == "APIC":
                        h = info["file_hash"] or "noHash"
                        cover_path = os.path.join(COVERS_DIR, f"{h}.jpg")
                        if not os.path.exists(cover_path):
                            with open(cover_path, "wb") as cf:
                                cf.write(tag.data)
                        info["cover"] = cover_path
                        break
            except Exception:
                pass
        elif ext == ".flac":
            audio = FLAC(path)
            info["duration"] = audio.info.length
            info["title"] = (audio.get("title") or [info["title"]])[0]
            info["artist"] = (audio.get("artist") or [""])[0]
            info["album"] = (audio.get("album") or [""])[0]
            info["year"] = (audio.get("date") or [""])[0]
            info["genre"] = (audio.get("genre") or [""])[0]
        else:
            f = MutagenFile(path, easy=True)
            if f:
                info["title"] = (f.get("title") or [info["title"]])[0]
                info["artist"] = (f.get("artist") or [""])[0]
                info["album"] = (f.get("album") or [""])[0]
                if getattr(f, "info", None):
                    info["duration"] = getattr(f.info, "length", 0.0)
    except Exception:
        pass
    return info


def scan_library():
    conn = db()
    exts = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
    for root, dirs, files in os.walk(MUSIC_DIR):
        if root.startswith(COVERS_DIR):
            continue
        for fn in files:
            if fn.lower().endswith(exts):
                full = os.path.join(root, fn)
                t = read_tags(full)
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO tracks
                           (path,title,artist,album,year,genre,duration,cover,file_hash,added)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (full, t["title"], t["artist"], t["album"], t["year"], t["genre"],
                         t["duration"], t["cover"], t["file_hash"], time.time()))
                except Exception:
                    pass
    conn.commit()
    conn.close()


def get_duplicates():
    conn = db()
    rows = conn.execute(
        "SELECT file_hash, COUNT(*) c FROM tracks WHERE file_hash IS NOT NULL "
        "GROUP BY file_hash HAVING c > 1").fetchall()
    groups = []
    for h, _ in rows:
        tracks = conn.execute(
            "SELECT id,path,title,artist FROM tracks WHERE file_hash=?", (h,)).fetchall()
        groups.append(tracks)
    conn.close()
    return groups


def get_stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    liked = conn.execute("SELECT COUNT(*) FROM tracks WHERE liked=1").fetchone()[0]
    duration = conn.execute("SELECT COALESCE(SUM(duration),0) FROM tracks").fetchone()[0]
    plays = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    playlists = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    top = conn.execute(
        """SELECT t.title, t.artist, COUNT(h.id) c FROM history h
           JOIN tracks t ON t.path=h.path GROUP BY h.path ORDER BY c DESC LIMIT 5""").fetchall()
    conn.close()
    hours = duration / 3600
    return {"total": total, "liked": liked, "hours": round(hours, 1),
            "plays": plays, "playlists": playlists, "top": top}


# ───────────────────────── Тексты песен / обложки из сети ─────────────────────────
def fetch_lyrics(artist, title):
    if not REQUESTS_OK or not artist or not title:
        return ""
    try:
        url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return (r.json().get("lyrics") or "").strip()
    except Exception:
        pass
    return ""


def fetch_cover_online(artist, title, save_path):
    if not REQUESTS_OK:
        return None
    try:
        q = urllib.parse.quote(f"{artist} {title}")
        url = f"https://itunes.apple.com/search?term={q}&media=music&limit=1"
        r = requests.get(url, timeout=8)
        results = r.json().get("results", [])
        if not results:
            return None
        art_url = results[0].get("artworkUrl100", "").replace("100x100", "400x400")
        if not art_url:
            return None
        img = requests.get(art_url, timeout=8).content
        with open(save_path, "wb") as f:
            f.write(img)
        return save_path
    except Exception:
        return None


# ───────────────────────── YouTube поиск/загрузка ─────────────────────────
def youtube_search(query, limit=15):
    if not YTDLP_OK:
        return []
    opts = {"quiet": True, "noplaylist": True, "skip_download": True, "extract_flat": "in_playlist"}
    results = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        for entry in info.get("entries", []):
            results.append({
                "id": entry.get("id"),
                "title": entry.get("title"),
                "duration": entry.get("duration"),
                "uploader": entry.get("uploader") or entry.get("channel"),
            })
    return results


def youtube_download(video_id, on_progress=None):
    if not YTDLP_OK:
        raise RuntimeError("yt-dlp не установлен")
    outtmpl = os.path.join(MUSIC_DIR, "%(title)s.%(ext)s")

    def hook(d):
        if not on_progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            done = d.get("downloaded_bytes", 0)
            pct = done / total
            speed = d.get("speed") or 0
            on_progress(pct, f"{pct*100:.0f}%  ({speed/1024:.0f} КБ/с)" if speed else f"{pct*100:.0f}%")
        elif d.get("status") == "finished":
            on_progress(1.0, "Обработка...")

    opts = {
        "quiet": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "progress_hooks": [hook],
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        return ydl.prepare_filename(info), info.get("title", ""), info.get("uploader", "")


# ───────────────────────── UI ─────────────────────────
KV_TEMPLATE = """
#:import dp kivy.metrics.dp

ScreenManager:
    LibraryScreen:
    SearchScreen:
    PlayerScreen:
    PlaylistsScreen:
    PlaylistDetailScreen:
    StatsScreen:
    DuplicatesScreen:
    SettingsScreen:

<TopBar@BoxLayout>:
    title: ""
    show_back: True
    size_hint_y: None
    height: dp(56)
    padding: dp(8)
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: app.c_panel
        Rectangle:
            pos: self.pos
            size: self.size
    Button:
        text: "<"
        size_hint_x: None
        width: dp(48) if root.show_back else 0
        opacity: 1 if root.show_back else 0
        disabled: not root.show_back
        on_release: app.root.current = "library"
    Label:
        text: root.title
        font_size: "18sp"
        bold: True
        color: app.c_text

<LibraryScreen>:
    name: "library"
    BoxLayout:
        orientation: "vertical"
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(8)
            spacing: dp(6)
            canvas.before:
                Color:
                    rgba: app.c_panel
                Rectangle:
                    pos: self.pos
                    size: self.size
            Label:
                text: "MusicBox Pro"
                font_size: "20sp"
                bold: True
                color: app.c_text
            Button:
                text: "Скан"
                size_hint_x: None
                width: dp(64)
                on_release: app.rescan_library()
            Button:
                text: "Плейл."
                size_hint_x: None
                width: dp(70)
                on_release: app.root.current = "playlists"
            Button:
                text: "Стат."
                size_hint_x: None
                width: dp(64)
                on_release: app.open_stats()
            Button:
                text: "YT"
                size_hint_x: None
                width: dp(44)
                on_release: app.root.current = "search"
            Button:
                text: "Set"
                size_hint_x: None
                width: dp(44)
                on_release: app.root.current = "settings"

        TextInput:
            id: lib_search
            hint_text: "Поиск в библиотеке..."
            multiline: False
            size_hint_y: None
            height: dp(44)
            on_text: app.filter_library(self.text)

        RecycleView:
            id: track_list
            viewclass: "TrackRow"
            RecycleBoxLayout:
                default_size: None, dp(70)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: "vertical"

<TrackRow@BoxLayout>:
    title: ""
    artist: ""
    path: ""
    liked: 0
    size_hint_y: None
    height: dp(70)
    padding: dp(10)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: app.c_panel
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"
        Label:
            text: root.title
            color: app.c_text
            halign: "left"
            valign: "middle"
            text_size: self.size
            shorten: True
        Label:
            text: root.artist or "Неизвестен"
            color: app.c_sub
            font_size: "12sp"
            halign: "left"
            valign: "middle"
            text_size: self.size
    Button:
        text: "L" if root.liked else "l"
        size_hint_x: None
        width: dp(40)
        on_release: app.toggle_like(root.path)
    Button:
        text: "+"
        size_hint_x: None
        width: dp(40)
        on_release: app.open_add_to_playlist(root.path)
    Button:
        text: ">"
        size_hint_x: None
        width: dp(44)
        on_release: app.play_track(root.path, root.title, root.artist)

<SearchScreen>:
    name: "search"
    BoxLayout:
        orientation: "vertical"
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            title: "Поиск YouTube"
        BoxLayout:
            size_hint_y: None
            height: dp(52)
            padding: dp(8)
            spacing: dp(8)
            TextInput:
                id: search_input
                hint_text: "Поиск на YouTube..."
                multiline: False
                on_text_validate: app.do_search(self.text)
            Button:
                text: "Найти"
                size_hint_x: None
                width: dp(90)
                on_release: app.do_search(search_input.text)
        Label:
            id: search_status
            text: ""
            size_hint_y: None
            height: dp(24)
            color: app.c_sub
        ProgressBar:
            id: dl_progress
            max: 1
            value: 0
            size_hint_y: None
            height: dp(6)
        RecycleView:
            id: search_list
            viewclass: "SearchRow"
            RecycleBoxLayout:
                default_size: None, dp(70)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: "vertical"

<SearchRow@BoxLayout>:
    title: ""
    uploader: ""
    video_id: ""
    size_hint_y: None
    height: dp(70)
    padding: dp(10)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: app.c_panel
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"
        Label:
            text: root.title
            color: app.c_text
            halign: "left"
            valign: "middle"
            text_size: self.size
            shorten: True
        Label:
            text: root.uploader or ""
            color: app.c_sub
            font_size: "12sp"
            halign: "left"
            valign: "middle"
            text_size: self.size
    Button:
        text: "Скач."
        size_hint_x: None
        width: dp(64)
        on_release: app.download_track(root.video_id, root.title)

<PlayerScreen>:
    name: "player"
    BoxLayout:
        orientation: "vertical"
        padding: dp(20)
        spacing: dp(14)
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            title: "Сейчас играет"
        Widget:
            id: cover_holder
            canvas:
                Color:
                    rgba: app.c_accent[0], app.c_accent[1], app.c_accent[2], 0.25
                Ellipse:
                    pos: self.center_x - dp(95), self.center_y - dp(95)
                    size: dp(190), dp(190)
                Color:
                    rgba: app.c_accent
                Ellipse:
                    pos: self.center_x - dp(58), self.center_y - dp(58)
                    size: dp(116), dp(116)
        Label:
            text: app.now_title
            font_size: "22sp"
            bold: True
            color: app.c_text
            size_hint_y: None
            height: dp(32)
        Label:
            text: app.now_artist
            font_size: "14sp"
            color: app.c_sub
            size_hint_y: None
            height: dp(22)
        Slider:
            id: seek
            min: 0
            max: 1
            value: app.progress
            on_touch_up: app.seek_to(self.value) if self.collide_point(*args[1].pos) else None
        BoxLayout:
            size_hint_y: None
            height: dp(64)
            spacing: dp(16)
            Button:
                text: "<<"
                on_release: app.prev_track()
            Button:
                text: "II" if app.is_playing else ">"
                on_release: app.toggle_play()
            Button:
                text: ">>"
                on_release: app.next_track()
        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(10)
            Button:
                text: "Текст песни"
                on_release: app.show_lyrics()
            Button:
                text: "L" if app.now_liked else "l"
                size_hint_x: None
                width: dp(56)
                on_release: app.toggle_like(app.now_path)

<PlaylistsScreen>:
    name: "playlists"
    BoxLayout:
        orientation: "vertical"
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            title: "Плейлисты"
        BoxLayout:
            size_hint_y: None
            height: dp(52)
            padding: dp(8)
            spacing: dp(8)
            TextInput:
                id: new_pl_name
                hint_text: "Название нового плейлиста"
                multiline: False
            Button:
                text: "Создать"
                size_hint_x: None
                width: dp(90)
                on_release: app.create_playlist(new_pl_name.text); new_pl_name.text = ""
        RecycleView:
            id: playlists_list
            viewclass: "PlaylistRow"
            RecycleBoxLayout:
                default_size: None, dp(60)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: "vertical"

<PlaylistRow@BoxLayout>:
    pl_id: 0
    pl_name: ""
    size_hint_y: None
    height: dp(60)
    padding: dp(10)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: app.c_panel
        Rectangle:
            pos: self.pos
            size: self.size
    Label:
        text: root.pl_name
        color: app.c_text
        halign: "left"
        valign: "middle"
        text_size: self.size
    Button:
        text: "Открыть"
        size_hint_x: None
        width: dp(90)
        on_release: app.open_playlist(root.pl_id, root.pl_name)
    Button:
        text: "X"
        size_hint_x: None
        width: dp(40)
        on_release: app.delete_playlist(root.pl_id)

<PlaylistDetailScreen>:
    name: "playlist_detail"
    BoxLayout:
        orientation: "vertical"
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            id: pd_top
            title: "Плейлист"
        RecycleView:
            id: pd_list
            viewclass: "TrackRow"
            RecycleBoxLayout:
                default_size: None, dp(70)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: "vertical"

<StatsScreen>:
    name: "stats"
    BoxLayout:
        orientation: "vertical"
        padding: dp(16)
        spacing: dp(10)
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            title: "Статистика"
        Label:
            id: stats_label
            text: app.stats_text
            color: app.c_text
            halign: "left"
            valign: "top"
            text_size: self.width, None
            markup: True

<DuplicatesScreen>:
    name: "duplicates"
    BoxLayout:
        orientation: "vertical"
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            title: "Дубликаты"
        RecycleView:
            id: dup_list
            viewclass: "DupRow"
            RecycleBoxLayout:
                default_size: None, dp(60)
                default_size_hint: 1, None
                size_hint_y: None
                height: self.minimum_height
                orientation: "vertical"

<DupRow@BoxLayout>:
    title: ""
    track_id: 0
    size_hint_y: None
    height: dp(60)
    padding: dp(10)
    canvas.before:
        Color:
            rgba: app.c_panel
        Rectangle:
            pos: self.pos
            size: self.size
    Label:
        text: root.title
        color: app.c_text
        halign: "left"
        valign: "middle"
        text_size: self.size
    Button:
        text: "Удалить"
        size_hint_x: None
        width: dp(90)
        on_release: app.delete_track(root.track_id)

<SettingsScreen>:
    name: "settings"
    BoxLayout:
        orientation: "vertical"
        padding: dp(16)
        spacing: dp(12)
        canvas.before:
            Color:
                rgba: app.c_bg
            Rectangle:
                pos: self.pos
                size: self.size
        TopBar:
            title: "Настройки"
        Label:
            text: "Тема оформления"
            color: app.c_text
            size_hint_y: None
            height: dp(28)
        GridLayout:
            cols: 2
            size_hint_y: None
            height: dp(100)
            spacing: dp(8)
            Button:
                text: "Dark"
                on_release: app.set_theme("Dark")
            Button:
                text: "Purple"
                on_release: app.set_theme("Purple")
            Button:
                text: "Green"
                on_release: app.set_theme("Green")
            Button:
                text: "Sunset"
                on_release: app.set_theme("Sunset")
        Button:
            text: "Найти дубликаты"
            size_hint_y: None
            height: dp(48)
            on_release: app.open_duplicates()
        Widget:
"""


class LibraryScreen(Screen):
    pass


class SearchScreen(Screen):
    pass


class PlayerScreen(Screen):
    pass


class PlaylistsScreen(Screen):
    pass


class PlaylistDetailScreen(Screen):
    pass


class StatsScreen(Screen):
    pass


class DuplicatesScreen(Screen):
    pass


class SettingsScreen(Screen):
    pass


class MusicBoxApp(App):
    now_title = StringProperty("Ничего не играет")
    now_artist = StringProperty("")
    now_path = StringProperty("")
    now_liked = BooleanProperty(False)
    progress = NumericProperty(0)
    is_playing = BooleanProperty(False)
    stats_text = StringProperty("")

    c_bg = ListProperty(get_color_from_hex(THEMES["Dark"]["bg"]))
    c_panel = ListProperty(get_color_from_hex(THEMES["Dark"]["panel"]))
    c_accent = ListProperty(get_color_from_hex(THEMES["Dark"]["accent"]))
    c_text = ListProperty(get_color_from_hex(THEMES["Dark"]["text"]))
    c_sub = ListProperty(get_color_from_hex(THEMES["Dark"]["sub"]))

    def build(self):
        self.settings = load_settings()
        self.apply_theme(self.settings.get("theme", "Dark"))
        Window.clearcolor = self.c_bg
        self.sound = None
        self.queue = []
        self.queue_index = 0
        self.full_library = []
        self.current_playlist_id = None
        self._toast_popup = None
        root = Builder.load_string(KV_TEMPLATE)
        Clock.schedule_once(lambda dt: self.rescan_library(), 0.3)
        Clock.schedule_interval(self._update_progress, 0.5)
        return root

    # ── темы ──
    def apply_theme(self, name):
        t = THEMES.get(name, THEMES["Dark"])
        self.c_bg = get_color_from_hex(t["bg"])
        self.c_panel = get_color_from_hex(t["panel"])
        self.c_accent = get_color_from_hex(t["accent"])
        self.c_text = get_color_from_hex(t["text"])
        self.c_sub = get_color_from_hex(t["sub"])

    def set_theme(self, name):
        self.apply_theme(name)
        Window.clearcolor = self.c_bg
        self.settings["theme"] = name
        save_settings(self.settings)

    # ── библиотека ──
    def rescan_library(self):
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        scan_library()
        Clock.schedule_once(lambda dt: self._refresh_library_view())

    def _refresh_library_view(self):
        conn = db()
        rows = conn.execute(
            "SELECT path,title,artist,liked FROM tracks ORDER BY title").fetchall()
        conn.close()
        self.full_library = [
            {"path": r[0], "title": r[1], "artist": r[2], "liked": r[3]} for r in rows]
        self.queue = list(self.full_library)
        self._set_track_view(self.full_library)

    def _set_track_view(self, items):
        view = self.root.get_screen("library").ids.track_list
        view.data = [
            {"title": t["title"], "artist": t["artist"], "path": t["path"], "liked": t["liked"]}
            for t in items]

    def filter_library(self, query):
        q = (query or "").lower().strip()
        if not q:
            self._set_track_view(self.full_library)
            return
        filtered = [t for t in self.full_library
                    if q in (t["title"] or "").lower() or q in (t["artist"] or "").lower()]
        self._set_track_view(filtered)

    def toggle_like(self, path):
        if not path:
            return
        conn = db()
        conn.execute("UPDATE tracks SET liked = 1 - liked WHERE path=?", (path,))
        conn.commit()
        row = conn.execute("SELECT liked FROM tracks WHERE path=?", (path,)).fetchone()
        conn.close()
        if path == self.now_path and row:
            self.now_liked = bool(row[0])
        self._refresh_library_view()

    def delete_track(self, track_id):
        conn = db()
        conn.execute("DELETE FROM tracks WHERE id=?", (track_id,))
        conn.commit()
        conn.close()
        self.open_duplicates()
        self._refresh_library_view()

    # ── плейлисты ──
    def create_playlist(self, name):
        name = (name or "").strip()
        if not name:
            return
        conn = db()
        try:
            conn.execute("INSERT INTO playlists(name) VALUES (?)", (name,))
            conn.commit()
        except Exception:
            pass
        conn.close()
        self._refresh_playlists_view()

    def _refresh_playlists_view(self):
        conn = db()
        rows = conn.execute("SELECT id,name FROM playlists ORDER BY name").fetchall()
        conn.close()
        view = self.root.get_screen("playlists").ids.playlists_list
        view.data = [{"pl_id": r[0], "pl_name": r[1]} for r in rows]

    def delete_playlist(self, pl_id):
        conn = db()
        conn.execute("DELETE FROM playlists WHERE id=?", (pl_id,))
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pl_id,))
        conn.commit()
        conn.close()
        self._refresh_playlists_view()

    def open_playlist(self, pl_id, pl_name):
        self.current_playlist_id = pl_id
        conn = db()
        rows = conn.execute(
            """SELECT t.path,t.title,t.artist,t.liked FROM tracks t
               JOIN playlist_tracks pt ON pt.track_id=t.id
               WHERE pt.playlist_id=? ORDER BY pt.position""", (pl_id,)).fetchall()
        conn.close()
        screen = self.root.get_screen("playlist_detail")
        screen.ids.pd_top.title = pl_name
        screen.ids.pd_list.data = [
            {"title": r[1], "artist": r[2], "path": r[0], "liked": r[3]} for r in rows]
        self.queue = [{"path": r[0], "title": r[1], "artist": r[2]} for r in rows]
        self.root.current = "playlist_detail"

    def open_add_to_playlist(self, path):
        conn = db()
        playlists = conn.execute("SELECT id,name FROM playlists ORDER BY name").fetchall()
        conn.close()
        if not playlists:
            self._toast("Сначала создайте плейлист")
            return
        from kivy.uix.button import Button
        box = BoxLayout(orientation="vertical", spacing=8, padding=8)
        popup = Popup(title="Добавить в плейлист", size_hint=(0.8, 0.6))
        for pid, pname in playlists:
            btn_box = BoxLayout(size_hint_y=None, height=44)
            b = Button(text=pname)

            def make_cb(pid=pid, path=path, popup=popup):
                def cb(*_a):
                    self._add_track_to_playlist(pid, path)
                    popup.dismiss()
                return cb
            b.bind(on_release=make_cb())
            btn_box.add_widget(b)
            box.add_widget(btn_box)
        popup.content = box
        popup.open()

    def _add_track_to_playlist(self, pl_id, path):
        conn = db()
        row = conn.execute("SELECT id FROM tracks WHERE path=?", (path,)).fetchone()
        if row:
            track_id = row[0]
            pos = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?",
                (pl_id,)).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position) VALUES (?,?,?)",
                (pl_id, track_id, pos))
            conn.commit()
        conn.close()
        self._toast("Добавлено")

    def _toast(self, text):
        if self._toast_popup:
            self._toast_popup.dismiss()
        popup = Popup(title="", content=Label(text=text), size_hint=(0.6, 0.15),
                       auto_dismiss=True)
        self._toast_popup = popup
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), 1.4)

    # ── статистика / дубликаты ──
    def open_stats(self):
        s = get_stats()
        top_lines = "\n".join(
            f"  {i+1}. {t[0]} — {t[1]} ({t[2]}x)" for i, t in enumerate(s["top"])) or "  нет данных"
        self.stats_text = (
            f"[b]Треков:[/b] {s['total']}\n"
            f"[b]Избранных:[/b] {s['liked']}\n"
            f"[b]Плейлистов:[/b] {s['playlists']}\n"
            f"[b]Часов музыки:[/b] {s['hours']}\n"
            f"[b]Всего прослушиваний:[/b] {s['plays']}\n\n"
            f"[b]Топ треков:[/b]\n{top_lines}")
        self.root.current = "stats"

    def open_duplicates(self):
        groups = get_duplicates()
        data = []
        for group in groups:
            for tid, path, title, artist in group:
                data.append({"track_id": tid, "title": f"{title} — {artist}"})
        self.root.get_screen("duplicates").ids.dup_list.data = data
        self.root.current = "duplicates"

    # ── YouTube поиск/скачивание ──
    def do_search(self, query):
        query = (query or "").strip()
        if not query:
            return
        status = self.root.get_screen("search").ids.search_status
        status.text = "Поиск..."
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query):
        try:
            results = youtube_search(query)
        except Exception as e:
            Clock.schedule_once(lambda dt: self._search_error(str(e)))
            return
        Clock.schedule_once(lambda dt: self._search_done(results))

    @mainthread
    def _search_error(self, msg):
        self.root.get_screen("search").ids.search_status.text = f"Ошибка: {msg[:60]}"

    @mainthread
    def _search_done(self, results):
        screen = self.root.get_screen("search")
        screen.ids.search_status.text = f"Найдено: {len(results)}"
        screen.ids.search_list.data = [
            {"title": r["title"], "uploader": r["uploader"] or "", "video_id": r["id"]}
            for r in results]

    def download_track(self, video_id, title):
        screen = self.root.get_screen("search")
        screen.ids.search_status.text = f"Скачивание: {title[:40]}..."
        screen.ids.dl_progress.value = 0
        threading.Thread(target=self._download_thread, args=(video_id,), daemon=True).start()

    def _download_thread(self, video_id):
        try:
            path, title, uploader = youtube_download(
                video_id, on_progress=lambda pct, label: Clock.schedule_once(
                    lambda dt: self._download_progress(pct, label)))
            Clock.schedule_once(lambda dt: self._download_done(path, title, uploader))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._search_error(str(e)))

    @mainthread
    def _download_progress(self, pct, label):
        screen = self.root.get_screen("search")
        screen.ids.dl_progress.value = pct
        screen.ids.search_status.text = label

    @mainthread
    def _download_done(self, path, title, uploader):
        screen = self.root.get_screen("search")
        screen.ids.search_status.text = "Готово ✓"
        screen.ids.dl_progress.value = 1
        threading.Thread(target=self._enrich_track, args=(path, title, uploader), daemon=True).start()
        self.rescan_library()

    def _enrich_track(self, path, title, uploader):
        time.sleep(1.0)
        cover_path = os.path.join(COVERS_DIR, f"{file_hash(path) or 'x'}.jpg")
        if not os.path.exists(cover_path):
            fetch_cover_online(uploader, title, cover_path)
        lyrics = fetch_lyrics(uploader, title)
        conn = db()
        conn.execute("UPDATE tracks SET cover=COALESCE(cover,?), lyrics=? WHERE path=?",
                     (cover_path if os.path.exists(cover_path) else None, lyrics, path))
        conn.commit()
        conn.close()

    # ── плеер ──
    def play_track(self, path, title, artist):
        idx = next((i for i, t in enumerate(self.queue) if t["path"] == path), 0)
        self.queue_index = idx
        self._load_and_play(path, title, artist)
        self.root.current = "player"

    def _load_and_play(self, path, title, artist):
        if self.sound:
            self.sound.stop()
            self.sound.unload()
        self.sound = SoundLoader.load(path)
        self.now_title = title
        self.now_artist = artist or "Неизвестен"
        self.now_path = path
        conn = db()
        row = conn.execute("SELECT liked FROM tracks WHERE path=?", (path,)).fetchone()
        self.now_liked = bool(row[0]) if row else False
        conn.execute("INSERT INTO history(path,played_at) VALUES (?,?)", (path, time.time()))
        conn.commit()
        conn.close()
        if self.sound:
            self.sound.volume = self.settings.get("volume", 0.8)
            self.sound.play()
            self.is_playing = True

    def toggle_play(self):
        if not self.sound:
            return
        if self.is_playing:
            self.sound.stop()
            self.is_playing = False
        else:
            self.sound.play()
            self.is_playing = True

    def next_track(self):
        if not self.queue:
            return
        self.queue_index = (self.queue_index + 1) % len(self.queue)
        t = self.queue[self.queue_index]
        self._load_and_play(t["path"], t["title"], t["artist"])

    def prev_track(self):
        if not self.queue:
            return
        self.queue_index = (self.queue_index - 1) % len(self.queue)
        t = self.queue[self.queue_index]
        self._load_and_play(t["path"], t["title"], t["artist"])

    def seek_to(self, value):
        if self.sound:
            self.sound.seek(value * self.sound.length)

    def _update_progress(self, dt):
        if self.sound and self.is_playing:
            pos = self.sound.get_pos() or 0
            length = self.sound.length or 1
            self.progress = pos / length if length else 0
            if length > 0 and pos >= length - 0.3:
                self.next_track()

    def show_lyrics(self):
        if not self.now_path:
            return
        conn = db()
        row = conn.execute("SELECT lyrics FROM tracks WHERE path=?", (self.now_path,)).fetchone()
        conn.close()
        lyrics = (row[0] if row else "") or ""
        if not lyrics:
            self._toast("Загрузка текста...")
            threading.Thread(target=self._fetch_lyrics_now, daemon=True).start()
            return
        self._show_lyrics_popup(lyrics)

    def _fetch_lyrics_now(self):
        lyrics = fetch_lyrics(self.now_artist, self.now_title) or "Текст не найден"
        conn = db()
        conn.execute("UPDATE tracks SET lyrics=? WHERE path=?", (lyrics, self.now_path))
        conn.commit()
        conn.close()
        Clock.schedule_once(lambda dt: self._show_lyrics_popup(lyrics))

    @mainthread
    def _show_lyrics_popup(self, lyrics):
        from kivy.uix.scrollview import ScrollView
        sv = ScrollView()
        lbl = Label(text=lyrics or "Текст не найден", size_hint_y=None, color=self.c_text,
                    halign="left", valign="top")
        lbl.bind(texture_size=lambda inst, val: setattr(lbl, "height", val[1]))
        lbl.bind(width=lambda inst, val: setattr(lbl, "text_size", (val, None)))
        sv.add_widget(lbl)
        Popup(title=self.now_title, content=sv, size_hint=(0.9, 0.8)).open()


if __name__ == "__main__":
    MusicBoxApp().run()
