"""播放守护：轮询 PotPlayer 状态，写入播放进度与观看会话。

设计成独立模块，由托盘常驻进程调用；主窗口只读数据库，两个进程互不干扰。
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import db, metadata, potplayer, scanner


class Watcher:
    def __init__(self, on_event=None):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: dict | None = None
        self.on_event = on_event
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        db.init_db()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="mv-watcher", daemon=True)
        self._thread.start()
        self._emit("started")

    def stop(self) -> None:
        self._stop_event.set()
        self._close_session()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self._emit("stopped")

    def _emit(self, kind: str, payload: dict | None = None) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload or {})
            except Exception:
                pass

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            interval = max(1, db.get_int("poll_interval_sec", 3))
            try:
                if db.get_int("recording_enabled", 1):
                    self._tick()
                else:
                    self._close_session()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop_event.wait(interval)

    def _resume_ms(self, position_ms: int, duration_ms: int, completed: int) -> int:
        """按三阈值决定「续播点」：片头太短 / 进度太低 / 已看完时返回 0。

        v0.1.6 起可配置：``ignore_seconds_at_start``（片头忽略秒数）、
        ``min_resume_percent``（最低进度百分比）、``ignore_percent_at_end``
        只影响 completed 判定（在 _tick 里算）。写成独立方法是为了
        ``_open_session`` / ``_close_session`` 兜底 / ``_tick`` 三处共用一套口径，
        避免「点开几秒就生成续播记录」。
        """
        if completed:
            return 0
        if position_ms < db.get_int("ignore_seconds_at_start", 30) * 1000:
            return 0
        if duration_ms and position_ms / duration_ms * 100 < db.get_int("min_resume_percent", 5):
            return 0
        return position_ms

    def _register_external(self, media_name: str) -> dict:
        """播放器打开的文件不在扫描库里时，先建一条占位记录，保证历史不丢。"""
        existing = db.row("SELECT * FROM media_files WHERE path = ?", (media_name,))
        if existing:
            return existing
        info = scanner.parse_filename(media_name)
        # 外部打开的文件也即时查一次离线词典；守护进程不发网络请求，避免阻塞轮询
        hit = metadata.match_one(info["title_guess"], allow_online=False)
        title_cn = hit["title_cn"] if hit.get("matched") else None
        match_by = hit["by"] if hit.get("matched") else None
        file_id = db.execute(
            "INSERT INTO media_files(path, filename, title_raw, title_guess, title_cn, match_by, "
            "season_no, episode_no, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'external')",
            (
                media_name, media_name, Path(media_name).stem, info["title_guess"],
                title_cn, match_by, info["season_no"], info["episode_no"],
            ),
        )
        return db.row("SELECT * FROM media_files WHERE id = ?", (file_id,))

    def _open_session(self, file_id: int, status: dict) -> None:
        stamp = db.now()
        # 会话区间记真实播放位置（from/to/max），续播点则按阈值过滤片头
        session_id = db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, from_ms, to_ms, max_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, stamp, status["position_ms"], status["position_ms"], status["position_ms"]),
        )
        resume = self._resume_ms(status["position_ms"], status["duration_ms"], 0)
        db.execute(
            "INSERT OR IGNORE INTO watch_progress"
            "(file_id, position_ms, duration_ms, percent, completed, play_count, first_played_at, last_played_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)",
            (file_id, resume, status["duration_ms"], status["percent"], stamp, stamp, stamp),
        )
        db.execute(
            "UPDATE watch_progress SET play_count = play_count + 1, first_played_at = COALESCE(first_played_at, ?) "
            "WHERE file_id = ?",
            (stamp, file_id),
        )
        self.current = {
            "file_id": file_id,
            "session_id": session_id,
            "started_at": stamp,
            "max_ms": status["position_ms"],
            "duration_ms": status["duration_ms"],
            # 首次轮询必须写一次进度/时长/完成判定，不能因为「位置没变」而跳过
            "last_written_ms": None,
            "last_state": status["state"],
        }
        self._emit("session_started", {"file_id": file_id})

    def _close_session(self) -> None:
        if not self.current:
            return
        cur = self.current
        to_ms = cur.get("last_written_ms")
        # 关闭 PotPlayer 时窗口句柄已消失，无法再回读最后位置。若这次会话还没
        # 成功写过进度（会话短于轮询间隔 / 首次读到 0），用本次见过的最大位置
        # 兜底，否则「继续观看」会因 position_ms=0 把刚看的这一集过滤掉。
        if not to_ms:
            fallback = int(cur.get("max_ms") or 0)
            if fallback > 0:
                completed_now = int(db.scalar(
                    "SELECT completed FROM watch_progress WHERE file_id = ?", (cur["file_id"],)) or 0)
                resume = self._resume_ms(fallback, int(cur.get("duration_ms") or 0), completed_now)
                # 已看完强制清 0；否则只在位置前进时更新，重看片头不抹掉已有续播点
                db.execute(
                    "UPDATE watch_progress SET "
                    "position_ms = CASE WHEN ? = 1 THEN 0 WHEN ? > COALESCE(position_ms,0) THEN ? "
                    "ELSE position_ms END, last_played_at = ?, updated_at = ? WHERE file_id = ?",
                    (completed_now, resume, resume, db.now(), db.now(), cur["file_id"]),
                )
            to_ms = fallback
            cur["last_written_ms"] = fallback
        db.execute(
            "UPDATE watch_sessions SET ended_at = ?, to_ms = ?, max_ms = ? WHERE id = ?",
            (db.now(), to_ms or 0, cur["max_ms"], cur["session_id"]),
        )
        self.current = None
        self._emit("session_ended", {"file_id": cur["file_id"]})

    def _tick(self) -> None:
        status = potplayer.read_status()
        if (
            not status.get("available")
            or status.get("state") == "stopped"
            or not status.get("media_name")
        ):
            self._close_session()
            return

        record = potplayer.resolve_media(status["media_name"])
        if record is None:
            record = self._register_external(status["media_name"])
        file_id = record["id"]

        if self.current and self.current["file_id"] != file_id:
            self._close_session()
        if not self.current:
            self._open_session(file_id, status)

        cur = self.current
        pos = status["position_ms"]
        cur["max_ms"] = max(cur["max_ms"], pos)
        moved = (cur["last_written_ms"] is None
                 or abs(pos - cur["last_written_ms"]) >= 1000)
        if moved or status["state"] != cur["last_state"]:
            complete_at = db.get_int("complete_percent", 90)
            end_ignore = db.get_int("ignore_percent_at_end", 8)
            percent = status["percent"] or 0.0
            # 四阈值判定：看够上限百分比，或只剩片尾 ignore_percent_at_end 以内 → 已看完
            completed = 1 if (
                percent >= complete_at or (percent > 0 and 100.0 - percent <= end_ignore)
            ) else 0
            resume = self._resume_ms(pos, status["duration_ms"], completed)
            db.execute(
                "UPDATE watch_progress SET "
                "position_ms = CASE WHEN ? = 1 THEN 0 WHEN ? > COALESCE(position_ms,0) THEN ? "
                "ELSE position_ms END, "
                "duration_ms = ?, percent = ?, completed = MAX(completed, ?), "
                "last_played_at = ?, updated_at = ? WHERE file_id = ?",
                (completed, resume, resume, status["duration_ms"], percent, completed,
                 db.now(), db.now(), file_id),
            )
            db.execute(
                "UPDATE watch_sessions SET to_ms = ?, max_ms = ? WHERE id = ?",
                (pos, cur["max_ms"], cur["session_id"]),
            )
            if status["duration_ms"] and not record.get("duration_ms"):
                db.execute(
                    "UPDATE media_files SET duration_ms = ?, updated_at = ? WHERE id = ?",
                    (status["duration_ms"], db.now(), file_id),
                )
            cur["last_written_ms"] = pos
            cur["last_state"] = status["state"]
            self._emit("progress", {"file_id": file_id, "percent": status["percent"]})
