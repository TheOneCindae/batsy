"""Persistent chat history and cancellable turns, backed by the existing memory database."""
import threading
import time


class ConversationService:
    def __init__(self, memory):
        self.memory = memory
        self.lock = threading.RLock()
        self.aborts = {}
        with memory.lock, memory.db:
            memory.db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                  id INTEGER PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                  pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
            """)
            cols = {r[1] for r in memory.db.execute("PRAGMA table_info(exchanges)")}
            for name, kind in (("conversation_id", "INTEGER"), ("reply_raw", "TEXT"), ("style", "TEXT"), ("brain", "TEXT")):
                if name not in cols:
                    memory.db.execute(f"ALTER TABLE exchanges ADD COLUMN {name} {kind}")
            memory.db.execute("CREATE INDEX IF NOT EXISTS exchanges_conv ON exchanges(conversation_id)")
            legacy = memory.db.execute("SELECT id,t FROM exchanges WHERE conversation_id IS NULL ORDER BY id").fetchall()
            days = {}
            for row in legacy:
                day = time.strftime("%Y-%m-%d", time.localtime(row["t"] or time.time()))
                if day not in days:
                    cur = memory.db.execute("INSERT INTO conversations(title,created,updated) VALUES(?,?,?)",
                                            (f"Voice, {day}", row["t"], row["t"]))
                    days[day] = cur.lastrowid
                memory.db.execute("UPDATE exchanges SET conversation_id=?, reply_raw=reply WHERE id=?",
                                  (days[day], row["id"]))
        self.active_id = self._get_active()
        if not self.active_id:
            self.active_id = self.create("New chat", activate=True)

    def _get_active(self):
        with self.memory.lock:
            row = self.memory.db.execute("SELECT value FROM state WHERE key='active_conversation'").fetchone()
        return int(row[0]) if row else None

    def activate(self, cid):
        with self.memory.lock, self.memory.db:
            if not self.memory.db.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone():
                raise KeyError("conversation not found")
            self.memory.db.execute("INSERT OR REPLACE INTO state VALUES('active_conversation',?)", (str(cid),))
        self.active_id = cid

    def create(self, title="New chat", activate=True):
        now = time.time()
        with self.memory.lock, self.memory.db:
            cur = self.memory.db.execute("INSERT INTO conversations(title,created,updated) VALUES(?,?,?)",
                                         (title[:100], now, now))
            cid = cur.lastrowid
        if activate:
            self.activate(cid)
        return cid

    def list(self, query=""):
        q = f"%{query.strip()}%"
        with self.memory.lock:
            rows = self.memory.db.execute("""SELECT c.id,c.title,c.created,c.updated,
              (SELECT user FROM exchanges e WHERE e.conversation_id=c.id ORDER BY e.id DESC LIMIT 1) last_user
              FROM conversations c WHERE c.archived=0 AND (?='' OR c.title LIKE ? OR EXISTS
              (SELECT 1 FROM exchanges e WHERE e.conversation_id=c.id AND (e.user LIKE ? OR e.reply_raw LIKE ?)))
              ORDER BY c.updated DESC LIMIT 100""", (query, q, q, q)).fetchall()
        return [dict(r) for r in rows]

    def messages(self, cid=None):
        cid = cid or self.active_id
        with self.memory.lock:
            rows = self.memory.db.execute("SELECT * FROM exchanges WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
        out = []
        for r in rows:
            if r["user"]:
                out.append({"role": "user", "text": r["user"], "id": r["id"]})
            if r["reply"]:
                out.append({"role": "assistant", "text": r["reply_raw"] or r["reply"], "id": r["id"],
                            "brain": r["brain"], "style": r["style"]})
        return out

    def begin_turn(self):
        tid = str(time.time_ns())
        abort = threading.Event()
        with self.lock:
            self.aborts[tid] = abort
        return tid, abort

    def finish_turn(self, tid):
        with self.lock:
            self.aborts.pop(tid, None)

    def stop(self, tid=None):
        with self.lock:
            targets = [self.aborts.get(tid)] if tid else list(self.aborts.values())
        for event in targets:
            if event:
                event.set()
