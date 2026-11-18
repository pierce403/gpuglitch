"""Transactional card identity, immutable baselines and a local experiment journal."""
import json
import platform
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def dump(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def identifier(value):
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned and cleaned.casefold() not in {
            "n/a", "[n/a]", "unknown", "none", "not supported", "[not supported]",
        }:
            return cleaned
    return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    baseline TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identifiers (
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    card_id TEXT NOT NULL REFERENCES cards(id),
    PRIMARY KEY (scope, kind, value)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    card_id TEXT REFERENCES cards(id),
    kind TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    state TEXT NOT NULL,
    inputs TEXT NOT NULL,
    result TEXT,
    environment TEXT NOT NULL
);
"""


class History:
    def __init__(self, path: Path, scope: str, *, readonly=False):
        self.scope = scope
        if readonly:
            self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, timeout=10)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version != 1 and (readonly or version != 0):
                raise ValueError(f"Unsupported history schema {version}")
            if version == 0:
                self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "PRAGMA user_version=1; COMMIT;")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def _card(self, sample):
        keys = [(kind, value) for kind in ("uuid", "serial", "asset_tag")
                if (value := identifier(sample.get(kind)))]
        matches = set()
        for kind, value in keys:
            row = self.db.execute(
                "SELECT card_id FROM identifiers WHERE scope=? AND kind=? AND value=?",
                (self.scope, kind, value),
            ).fetchone()
            if row:
                matches.add(row[0])
        if len(matches) > 1:
            raise ValueError("The supplied identifiers match different cards in this inventory")
        if matches:
            card_id = matches.pop()
            previous = dict(self.db.execute(
                "SELECT kind, value FROM identifiers WHERE card_id=?", (card_id,)
            ).fetchall())
            if any(kind in previous and previous[kind] != value for kind, value in keys):
                raise ValueError("Card identity conflicts with a previously recorded identifier")
        else:
            card_id = "card-" + uuid.uuid4().hex
            self.db.execute("INSERT INTO cards VALUES(?,?,?,?)",
                            (card_id, self.scope, now(), dump(sample)))
        for kind, value in keys:
            self.db.execute("INSERT OR IGNORE INTO identifiers VALUES(?,?,?,?)",
                            (self.scope, kind, value, card_id))
        return card_id

    def _event(self, card_id, kind, state, inputs, result=None):
        started = now()
        environment = {"ga100ctl": __version__, "host": platform.node(),
                       "kernel": platform.release(), "python": sys.version.split()[0]}
        return self.db.execute(
            "INSERT INTO events(scope, card_id, kind, started, finished, state, inputs, result, environment) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.scope, card_id, kind, started, None if state == "running" else started,
             state, dump(inputs), dump(result) if result is not None else None, dump(environment)),
        ).lastrowid

    def begin(self, kind, inputs, sample=None, peer=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            card_id = self._card(sample) if sample is not None else None
            if sample is not None:
                self._event(card_id, "inspection", "recorded", {}, sample)
            if peer is not None:
                peer_id = self._card(peer)
                if peer_id == card_id:
                    raise ValueError("Target and peer resolve to the same inventory entry")
                self._event(peer_id, "inspection", "recorded", {}, peer)
                inputs = {**inputs, "peer_card_id": peer_id}
            return card_id, self._event(card_id, kind, "running", inputs)

    def inspect(self, sample):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            card_id = self._card(sample)
            event_id = self._event(card_id, "inspection", "recorded", {}, sample)
        return card_id, event_id

    def finish(self, event_id, state, result):
        with self.db:
            updated = self.db.execute(
                "UPDATE events SET state=?, result=?, finished=? WHERE id=? AND scope=? AND state='running'",
                (state, dump(result), now(), event_id, self.scope),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Cannot finish event {event_id}")

    def _require_card(self, card_id):
        if not self.db.execute("SELECT 1 FROM cards WHERE id=? AND scope=?",
                               (card_id, self.scope)).fetchone():
            raise ValueError(f"Unknown card in this inventory: {card_id}")

    def note(self, card_id, topic, message):
        with self.db:
            self._require_card(card_id)
            return self._event(card_id, "operator-note", "recorded", {"topic": topic}, {"text": message})

    def report(self, card_id=None):
        if card_id:
            self._require_card(card_id)
        with self.db:
            self.db.execute("BEGIN")
            args = (self.scope, card_id, card_id)
            cards = [dict(r) for r in self.db.execute(
                "SELECT id,first_seen,baseline FROM cards WHERE scope=? AND (? IS NULL OR id=?) "
                "ORDER BY first_seen,id", args)]
            events = [dict(r) for r in self.db.execute(
                "SELECT id,card_id,kind,started,finished,state,inputs,result,environment FROM events "
                "WHERE scope=? AND (? IS NULL OR card_id=? "
                "OR (kind='benchmark' AND json_extract(inputs,'$.peer_card_id')=?)) ORDER BY id",
                (*args, card_id))]
        for card in cards:
            card["baseline"] = json.loads(card["baseline"])
        for event in events:
            for key in ("inputs", "result", "environment"):
                if event[key] is not None:
                    event[key] = json.loads(event[key])
        return {"schema_version": 1, "source": self.scope, "cards": cards, "events": events}
