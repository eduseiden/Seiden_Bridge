"""Saída HTTPS resiliente do Seiden Bridge.

O Bridge continua sendo edge-first: eventos são produzidos localmente e a saída
cloud é apenas mais um sink. O store-and-forward usa SQLite em /data, portanto
sobrevive a restart/update do add-on.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

DEFAULT_QUEUE_PATH = Path('/data/seiden_bridge_upstream.db')


@dataclass(frozen=True)
class UpstreamSettings:
    enabled: bool = False
    endpoint: str = ''
    token: str = ''
    timeout: int = 15
    verify_tls: bool = True
    retry_interval: int = 5
    max_retry_interval: int = 300
    retention_days: int = 7
    queue_max_mb: int = 64
    queue_path: Path = DEFAULT_QUEUE_PATH


class UpstreamClient:
    def __init__(self, settings: UpstreamSettings, logger: logging.Logger | None = None):
        self.settings = settings
        self.enabled = settings.enabled
        self.endpoint = settings.endpoint
        self.queue_path = settings.queue_path
        self.log = logger or logging.getLogger('seiden_bridge.upstream')
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        if self.enabled:
            self._init_db()

    @classmethod
    def from_config(cls, config: dict[str, Any], logger: logging.Logger | None = None) -> 'UpstreamClient':
        nested = config.get('upstream') if isinstance(config.get('upstream'), dict) else {}
        def pick(key: str, default: Any) -> Any:
            flat = f'upstream_{key}'
            if flat in config:
                return config.get(flat)
            return nested.get(key, default)
        endpoint = str(pick('endpoint', '') or '').strip()
        enabled = bool(pick('enabled', False))
        if enabled and not endpoint:
            raise ValueError('upstream_enabled=true exige upstream_endpoint')
        if endpoint and not endpoint.startswith(('http://','https://')):
            raise ValueError('upstream_endpoint deve iniciar com http:// ou https://')
        settings = UpstreamSettings(
            enabled=enabled,
            endpoint=endpoint,
            token=str(pick('token', '') or '').strip(),
            timeout=max(1, int(pick('timeout', 15))),
            verify_tls=bool(pick('verify_tls', True)),
            retry_interval=max(1, int(pick('retry_interval', 5))),
            max_retry_interval=max(1, int(pick('max_retry_interval', 300))),
            retention_days=max(1, int(pick('retention_days', 7))),
            queue_max_mb=max(1, int(pick('queue_max_mb', 64))),
            queue_path=Path(str(pick('queue_path', DEFAULT_QUEUE_PATH) or DEFAULT_QUEUE_PATH)),
        )
        return cls(settings, logger)

    def _connect(self) -> sqlite3.Connection:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.queue_path, timeout=10)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS queue(
                event_id TEXT PRIMARY KEY,
                event_type TEXT,
                payload TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_upstream_next ON queue(next_attempt,created_at)')

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._worker, daemon=True, name='bridge-upstream')
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def submit(self, payload: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        event_id = str(payload.get('event_id') or '').strip()
        if not event_id:
            self.log.warning('[UPSTREAM] Evento sem event_id não foi enviado: %s', payload.get('event_type'))
            return False
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), default=str)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO queue(event_id,event_type,payload,size_bytes,created_at,next_attempt) VALUES(?,?,?,?,?,?)',
                (event_id, str(payload.get('event_type') or ''), raw, len(raw.encode('utf-8')), now, now),
            )
            self._prune(conn, now)
        self._wake.set()
        return True

    def _prune(self, conn: sqlite3.Connection, now: float) -> None:
        cutoff = now - self.settings.retention_days * 86400
        removed = conn.execute('DELETE FROM queue WHERE created_at < ?', (cutoff,)).rowcount
        max_bytes = self.settings.queue_max_mb * 1024 * 1024
        total = int(conn.execute('SELECT COALESCE(SUM(size_bytes),0) FROM queue').fetchone()[0])
        dropped = 0
        while total > max_bytes:
            row = conn.execute('SELECT event_id,size_bytes FROM queue ORDER BY created_at LIMIT 1').fetchone()
            if not row: break
            conn.execute('DELETE FROM queue WHERE event_id=?', (row[0],))
            total -= int(row[1]); dropped += 1
        if removed or dropped:
            self.log.warning('[UPSTREAM] Retenção/fila removeu %d evento(s) vencidos e %d por limite', removed, dropped)

    def _next(self) -> tuple[str,str,int] | None:
        now=time.time()
        with self._connect() as conn:
            row=conn.execute('SELECT event_id,payload,attempts FROM queue WHERE next_attempt<=? ORDER BY created_at LIMIT 1',(now,)).fetchone()
        return (str(row[0]),str(row[1]),int(row[2])) if row else None

    def _deliver(self, event_id: str, raw: str, attempts: int) -> None:
        headers={'Content-Type':'application/json','User-Agent':'Seiden-Bridge/0.18.0'}
        if self.settings.token:
            headers['Authorization']=f'Bearer {self.settings.token}'
        try:
            response=requests.post(self.endpoint,headers=headers,data=raw.encode('utf-8'),timeout=self.settings.timeout,verify=self.settings.verify_tls)
            if 200 <= response.status_code < 300:
                with self._connect() as conn: conn.execute('DELETE FROM queue WHERE event_id=?',(event_id,))
                self.log.debug('[UPSTREAM] entregue | event_id=%s | HTTP %s',event_id,response.status_code)
                return
            error=f'HTTP {response.status_code}'
        except requests.RequestException as exc:
            error=str(exc)
        attempts += 1
        delay=min(self.settings.max_retry_interval,self.settings.retry_interval*(2 ** min(attempts-1,8)))
        with self._connect() as conn:
            conn.execute('UPDATE queue SET attempts=?,next_attempt=? WHERE event_id=?',(attempts,time.time()+delay,event_id))
        if attempts == 1 or attempts % 5 == 0:
            self.log.warning('[UPSTREAM] entrega pendente | event_id=%s | tentativa=%d | retry=%ss | %s',event_id,attempts,delay,error)

    def _worker(self) -> None:
        self.log.info('[UPSTREAM] worker iniciado')
        while not self._stop.is_set():
            item=self._next()
            if item is None:
                self._wake.wait(1.0); self._wake.clear(); continue
            self._deliver(*item)
        self.log.info('[UPSTREAM] worker encerrado')

    def queue_stats(self) -> dict[str, int]:
        if not self.enabled: return {'count':0,'bytes':0}
        with self._connect() as conn:
            row=conn.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM queue').fetchone()
        return {'count':int(row[0]),'bytes':int(row[1])}
