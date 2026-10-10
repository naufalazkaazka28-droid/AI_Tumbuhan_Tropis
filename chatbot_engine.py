#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chatbot Tumbuhan Tropis - engine heuristik (tanpa dependensi eksternal)

Pemetaan ke arsitektur:
  [1] InputProcessor      -> Input Processing & Normalization Layer
  [2] HeuristicEngine     -> Heuristic & Pattern Matching Engine
  [3] KnowledgeBase       -> Rule & Knowledge Base (tropical_plants_kb.json)
  [4] ResponseBuilder     -> Match Found  -> Response Builder
  [5] FallbackHandler +
      HandoffManager      -> No Match     -> Fallback & Human Handoff
UI (Web / WhatsApp / API) ada di server.py dan hanya memanggil ChatbotEngine.reply().
"""

import difflib
import hashlib
import json
import re
import sys
import threading
import time
import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
KB_PATH = BASE_DIR / "tropical_plants_kb.json"
DATA_DIR = BASE_DIR / "data"

REQUIRED_PLANT_FIELDS = ("ilmiah", "famili", "alias", "ciri", "habitat",
                         "manfaat", "perawatan", "keamanan")
REQUIRED_SECTIONS = ("settings", "stopwords", "slang", "domain", "intents",
                     "messages", "rules", "plants", "human_request",
                     "compare_keywords", "sensitive_pattern")
MATCH_KINDS = {"answer", "compare", "rule", "smalltalk"}


def basic_normalize(text: str) -> str:
    """Huruf kecil, buang aksen/tanda baca/URL, rapikan huruf berulang."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    return re.sub(r"\s+", " ", text).strip()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


# ======================================================================
# [3] RULE & KNOWLEDGE BASE
# ======================================================================
class KnowledgeBase:
    def __init__(self, path=KB_PATH):
        self.path = Path(path)
        self._mtime = 0.0
        self.reload()

    def reload(self):
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self._validate(data)
        self.data = data
        self.settings = data["settings"]
        self.plants = data["plants"]
        self.intents = data["intents"]
        self.messages = data["messages"]
        self.stopwords = set(data["stopwords"])
        self.slang = data["slang"]
        self.domain_terms = data["domain"]["terms"]
        self.offtopic_phrases = data["domain"]["offtopic_phrases"]
        self.out_of_scope = data["domain"]["out_of_scope_plants"]
        self.rules = sorted(data["rules"], key=lambda r: -r.get("priority", 0))
        self._compile()
        self._mtime = self.path.stat().st_mtime

    def refresh_if_changed(self):
        try:
            if self.path.stat().st_mtime != self._mtime:
                self.reload()
        except Exception as exc:  # KB rusak -> tetap pakai versi lama
            print(f"[KB] gagal memuat ulang, memakai versi lama: {exc}", file=sys.stderr)
            self._mtime = self.path.stat().st_mtime

    @staticmethod
    def _validate(data):
        for sec in REQUIRED_SECTIONS:
            if sec not in data:
                raise ValueError(f"KB: bagian '{sec}' tidak ada")
        for name, p in data["plants"].items():
            for fld in REQUIRED_PLANT_FIELDS:
                if fld not in p:
                    raise ValueError(f"KB: tumbuhan '{name}' tidak punya field '{fld}'")
        for r in data["rules"]:
            for fld in ("id", "type", "patterns", "response"):
                if fld not in r:
                    raise ValueError(f"KB: rule {r.get('id', '?')} tidak punya '{fld}'")
        for name, spec in data["intents"].items():
            for fld in ("keywords", "field", "label", "hint"):
                if fld not in spec:
                    raise ValueError(f"KB: intent '{name}' tidak punya '{fld}'")

    def _compile(self):
        try:
            for r in self.rules:
                r["_rx"] = [re.compile(p) for p in r["patterns"]]
            self.human_rx = [re.compile(p) for p in self.data["human_request"]["patterns"]]
            self.sensitive_rx = re.compile(self.data["sensitive_pattern"])
            kws = [re.escape(k) for k in self.data["compare_keywords"]]
            self.compare_rx = re.compile(r"\b(" + "|".join(kws) + r")\b")
        except re.error as exc:
            raise ValueError(f"KB: regex tidak valid: {exc}")

        self.name_to_key = {}
        for key, p in self.plants.items():
            for n in [key] + p["alias"] + [p["ilmiah"]]:
                self.name_to_key[basic_normalize(n)] = key
        self.phrases = sorted(self.name_to_key, key=len, reverse=True)

        for spec in self.intents.values():
            spec["_rx"] = [re.compile(r"(?<![a-z])" + re.escape(k)) for k in spec["keywords"]]
        self.intent_words = {w for s in self.intents.values()
                             for k in s["keywords"] for w in k.split()}

        vocab = set(self.stopwords) | set(self.data.get("extra_vocab", []))
        vocab |= {w for ph in self.phrases for w in ph.split()}
        vocab |= set(self.domain_terms) | self.intent_words
        vocab |= {w for v in self.slang.values() for w in v.split()}
        self.vocab = {w for w in vocab if len(w) >= 3}
        self.vocab_list = sorted(self.vocab)

    def plant_list(self):
        return ", ".join(k.title() for k in self.plants)


# ======================================================================
# Struktur data
# ======================================================================
@dataclass
class Processed:
    raw: str
    norm: str
    words: list
    tokens: list
    empty: bool


@dataclass
class Match:
    kind: str
    confidence: float = 0.0
    intents: list = field(default_factory=list)
    plants: list = field(default_factory=list)
    rule: Optional[dict] = None
    reason: str = ""


@dataclass
class Reply:
    text: str
    kind: str
    intent: Optional[str] = None
    confidence: float = 0.0
    plant: Optional[str] = None
    handoff: bool = False
    ticket_id: Optional[str] = None


@dataclass
class Session:
    id: str
    channel: str = "web"
    channel_user: Optional[str] = None
    last_plant: Optional[str] = None
    fail_count: int = 0
    state: str = "bot"                 # bot | handoff
    ticket_id: Optional[str] = None
    asked: set = field(default_factory=set)
    history: deque = field(default_factory=lambda: deque(maxlen=12))
    outbox: list = field(default_factory=list)
    stamps: deque = field(default_factory=deque)
    last_seen: float = field(default_factory=time.time)


# ======================================================================
# [1] INPUT PROCESSING & NORMALIZATION LAYER
# ======================================================================
class InputProcessor:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def _correct(self, w):
        kb = self.kb
        if w in kb.vocab or len(w) < 5 or w.isdigit():
            return w
        m = difflib.get_close_matches(w, kb.vocab_list, n=1, cutoff=kb.settings["fuzzy_cutoff"])
        return m[0] if m else w

    def process(self, text) -> Processed:
        kb = self.kb
        raw = (text if isinstance(text, str) else "")[: kb.settings["max_input_chars"]]
        words = []
        for w in basic_normalize(raw).split():
            for part in kb.slang.get(w, w).split():
                words.append(self._correct(part))
        tokens = [w for w in words if w not in kb.stopwords and len(w) > 1]
        norm = " ".join(words)
        return Processed(raw=raw, norm=norm, words=words, tokens=tokens, empty=not norm)


# ======================================================================
# [2] HEURISTIC & PATTERN MATCHING ENGINE
# ======================================================================
class HeuristicEngine:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    # --- komponen heuristik ------------------------------------------
    def find_plants(self, norm):
        found, work = [], f" {norm} "
        for phrase in self.kb.phrases:
            m = re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", work)
            if m:
                found.append((m.start(), self.kb.name_to_key[phrase]))
                work = work[: m.start()] + " " * (m.end() - m.start()) + work[m.end():]
        keys = []
        for _, k in sorted(found):
            if k not in keys:
                keys.append(k)
        return keys

    def detect_intents(self, norm):
        scores = {}
        for name, spec in self.kb.intents.items():
            s = sum(1 for rx in spec["_rx"] if rx.search(norm))
            if s:
                scores[name] = s
        if not scores:
            return []
        best = max(scores.values())
        return [n for n, s in scores.items() if s == best][:2]

    def domain_score(self, proc):
        terms = self.kb.domain_terms
        return sum(1 for t in proc.tokens if any(t.startswith(d) for d in terms))

    def leftover(self, proc):
        """Token yang tidak dikenali sebagai kata intent atau istilah domain."""
        words = self.kb.intent_words
        out = []
        for t in proc.tokens:
            if any(t.startswith(d) for d in self.kb.domain_terms):
                continue
            if any((len(w) >= 4 and w in t) or (len(t) >= 4 and t in w) or t == w for w in words):
                continue
            out.append(t)
        return out

    # --- alur pencocokan ----------------------------------------------
    def match(self, proc: Processed, session: Session) -> Match:
        kb, norm = self.kb, proc.norm
        n_words = len(proc.words)

        if any(rx.search(norm) for rx in kb.human_rx):
            return Match("human_request", 1.0)

        plants = self.find_plants(norm)

        for term in kb.out_of_scope:
            if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", norm):
                return Match("out_of_scope", 0.95, reason=term)

        if not plants:
            for phrase in kb.offtopic_phrases:
                if phrase in norm:
                    return Match("offtopic", 1.0, reason=phrase)

        for rule in kb.rules:                       # smalltalk
            if rule["type"] != "smalltalk":
                continue
            limit = rule.get("max_words", kb.settings["greeting_max_words"])
            if n_words <= limit and all(rx.search(norm) for rx in rule["_rx"]):
                return Match("smalltalk", 1.0, rule=rule)

        intents = self.detect_intents(norm)

        if len(plants) >= 2 and kb.compare_rx.search(norm):
            return Match("compare", 0.95, intents=intents[:1], plants=plants[:3])

        for rule in kb.rules:                       # faq / glossary / symptom
            if rule["type"] == "smalltalk":
                continue
            if rule.get("no_plant") and plants:
                continue
            if all(rx.search(norm) for rx in rule["_rx"]):
                return Match("rule", 0.9, rule=rule, plants=plants[:1])

        if plants:
            return Match("answer", 1.0 if intents else 0.75, intents=intents, plants=plants[:1])

        left = self.leftover(proc)
        if intents and not left:                    # pertanyaan lanjutan
            if session.last_plant:
                return Match("answer", 0.7, intents=intents, plants=[session.last_plant],
                             reason="konteks sesi")
            return Match("clarify", 0.5)
        if intents:
            return Match("no_match", 0.2, reason="entitas tidak dikenal: " + ", ".join(left[:3]))
        if self.domain_score(proc) >= kb.settings["domain_min_score"]:
            return Match("no_match", 0.3, reason="istilah domain tanpa jawaban")
        return Match("offtopic", 0.9, reason="tidak ada sinyal tumbuhan")


# ======================================================================
# Response Builder  (Match Found)
# ======================================================================
class ResponseBuilder:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def _value(self, plant, intent):
        return plant[self.kb.intents[intent]["field"]]

    def build(self, m: Match, s: Session, proc: Processed) -> Reply:
        if m.kind in ("smalltalk", "rule"):
            return self._rule(m, s)
        if m.kind == "compare":
            return self._compare(m)
        return self._answer(m, s, proc)

    def _rule(self, m, s):
        text = m.rule["response"].replace("{daftar_tumbuhan}", self.kb.plant_list())
        plant = None
        if m.rule["type"] == "symptom" and m.plants:
            plant = m.plants[0]
            s.last_plant = plant
            text = f"[{plant.title()}] {text}"
        return Reply(text, m.kind, intent=m.rule["id"], confidence=m.confidence, plant=plant)

    def _compare(self, m):
        kb = self.kb
        fields = m.intents or ["ciri", "habitat", "manfaat"]
        blocks = []
        for key in m.plants:
            p = kb.plants[key]
            lines = "\n".join(f"{kb.intents[i]['label']}: {self._value(p, i)}" for i in fields)
            blocks.append(kb.messages["compare_item"].format(
                nama=key.title(), ilmiah=p["ilmiah"], lines=lines))
        return Reply("\n\n".join(blocks), "compare", intent=",".join(fields),
                     confidence=m.confidence, plant=m.plants[0])

    def _answer(self, m, s, proc):
        kb, msg = self.kb, self.kb.messages
        key = m.plants[0]
        p = kb.plants[key]
        s.last_plant = key
        if m.intents:
            text = "\n\n".join(msg["answer"].format(
                nama=key.title(), ilmiah=p["ilmiah"], label=kb.intents[i]["label"],
                value=self._value(p, i)) for i in m.intents)
            for i in m.intents:
                s.asked.add((key, i))
        else:
            text = msg["profile"].format(nama=key.title(), ilmiah=p["ilmiah"], famili=p["famili"],
                                         ciri=p["ciri"], habitat=p["habitat"], manfaat=p["manfaat"])
            s.asked.update({(key, "ciri"), (key, "habitat"), (key, "manfaat")})

        asked_now = set(m.intents)
        if p.get("herbal") and (not m.intents or asked_now & {"manfaat", "keamanan"}):
            text += "\n\n" + msg["herbal_note"]
        if kb.sensitive_rx.search(proc.norm) and (not m.intents or asked_now & {"manfaat", "keamanan"}):
            text += "\n" + msg["sensitive_note"]

        options = [i for i in ("manfaat", "perawatan", "habitat", "keamanan", "ciri")
                   if (key, i) not in s.asked][:2]
        if options:
            text += "\n\n" + msg["suggest"].format(opsi=", ".join(kb.intents[i]["hint"] for i in options))
        return Reply(text, "answer", intent=",".join(m.intents) or "profile",
                     confidence=m.confidence, plant=key)


# ======================================================================
# Human handoff
# ======================================================================
class HandoffManager:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tickets = {}

    def _log(self, event, **data):
        try:
            with open(self.dir / "handoff_log.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": utcnow(), "event": event, **data}, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[handoff] gagal menulis log: {exc}", file=sys.stderr)

    def create(self, session: Session, reason: str):
        tid = "T" + uuid.uuid4().hex[:8].upper()
        t = {"id": tid, "channel": session.channel, "reason": reason, "status": "open",
             "created": utcnow(), "_sid": session.id,
             "messages": [{"role": r, "text": x, "ts": utcnow()} for r, x in session.history]}
        self.tickets[tid] = t
        self._log("created", ticket=tid, session=hash_id(session.id), reason=reason)
        return t

    def add_message(self, tid, role, text):
        t = self.tickets.get(tid)
        if t:
            t["messages"].append({"role": role, "text": text, "ts": utcnow()})
            self._log("message", ticket=tid, role=role, text=text[:500])

    def get(self, tid):
        return self.tickets.get(tid)

    def close(self, tid):
        t = self.tickets.get(tid)
        if t:
            t["status"] = "closed"
            self._log("closed", ticket=tid)
        return t

    def list(self, status="open"):
        return [{k: v for k, v in t.items() if not k.startswith("_")}
                for t in self.tickets.values() if status == "all" or t["status"] == status]


class SessionStore:
    def __init__(self, ttl, max_sessions):
        self.ttl, self.max = ttl, max_sessions
        self._s = {}

    def _purge(self, now):
        for sid in [k for k, v in self._s.items()
                    if v.state != "handoff" and now - v.last_seen > self.ttl]:
            del self._s[sid]
        while len(self._s) >= self.max:
            idle = [k for k, v in self._s.items() if v.state != "handoff"]
            if not idle:
                break
            del self._s[min(idle, key=lambda k: self._s[k].last_seen)]

    def get(self, sid, channel="web", channel_user=None):
        now = time.time()
        if len(self._s) >= self.max:
            self._purge(now)
        s = self._s.get(sid)
        if s is None or (s.state != "handoff" and now - s.last_seen > self.ttl):
            s = Session(id=sid, channel=channel, channel_user=channel_user)
            self._s[sid] = s
        s.last_seen = now
        if channel_user:
            s.channel_user = channel_user
        return s

    def peek(self, sid):
        return self._s.get(sid)


# ======================================================================
# Fallback & Human Handoff  (No Match)
# ======================================================================
class FallbackHandler:
    def __init__(self, engine):
        self.e = engine

    def _log_unanswered(self, s, m, proc):
        try:
            with open(self.e.data_dir / "unanswered.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": utcnow(), "session": hash_id(s.id), "kind": m.kind,
                                    "reason": m.reason, "text": proc.raw[:200]},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass

    def handle(self, m: Match, s: Session, proc: Processed) -> Reply:
        kb = self.e.kb
        msgs, lst = kb.messages, kb.plant_list()
        if m.kind == "human_request":
            return self.e.start_handoff(s, "permintaan pengguna")
        if m.kind == "offtopic":
            return Reply(msgs["offtopic"], "offtopic", confidence=m.confidence)
        if m.kind == "out_of_scope":
            return Reply(msgs["out_of_scope"].format(term=m.reason, daftar=lst), "out_of_scope",
                         confidence=m.confidence)
        if m.kind == "clarify":
            return Reply(msgs["clarify"].format(daftar=lst), "clarify", confidence=m.confidence)

        s.fail_count += 1                                   # no_match
        self._log_unanswered(s, m, proc)
        limit = kb.settings["handoff_after_failures"]
        if s.fail_count >= limit:
            return self.e.start_handoff(s, "pertanyaan tidak terjawab berulang")
        text = msgs["no_match"].format(daftar=lst)
        if s.fail_count == limit - 1:
            text += "\n\n" + msgs["handoff_offer"]
        return Reply(text, "no_match", confidence=m.confidence)


# ======================================================================
# Orkestrator
# ======================================================================
class ChatbotEngine:
    def __init__(self, kb_path=KB_PATH, data_dir=DATA_DIR):
        self.kb = KnowledgeBase(kb_path)
        self.data_dir = Path(data_dir)
        self.processor = InputProcessor(self.kb)
        self.heuristic = HeuristicEngine(self.kb)
        self.builder = ResponseBuilder(self.kb)
        self.handoff = HandoffManager(self.data_dir)
        self.fallback = FallbackHandler(self)
        st = self.kb.settings
        self.sessions = SessionStore(st["session_ttl_seconds"], st["max_sessions"])
        self._lock = threading.RLock()

    # --- API publik -----------------------------------------------------
    def reply(self, session_id, text, channel="web", channel_user=None) -> Reply:
        with self._lock:
            self.kb.refresh_if_changed()
            msgs = self.kb.messages
            s = self.sessions.get(session_id, channel, channel_user)
            if not self._allow(s):
                return Reply(msgs["rate_limited"], "rate_limited")
            proc = self.processor.process(text)
            if proc.empty:
                return Reply(msgs["empty"], "empty")
            s.history.append(("user", proc.raw[:300]))

            if s.state == "handoff":
                r = self._handoff_mode(s, proc)
            else:
                m = self.heuristic.match(proc, s)
                if m.kind in MATCH_KINDS and m.confidence < self.kb.settings["min_confidence"]:
                    m = Match("clarify", m.confidence)
                if m.kind in MATCH_KINDS:
                    s.fail_count = 0
                    r = self.builder.build(m, s, proc)
                else:
                    r = self.fallback.handle(m, s, proc)
            s.history.append(("bot", r.text[:300]))
            return r

    def start_handoff(self, s: Session, reason: str) -> Reply:
        t = self.handoff.create(s, reason)
        s.state, s.ticket_id = "handoff", t["id"]
        return Reply(self.kb.messages["handoff_created"].format(ticket=t["id"]), "handoff",
                     handoff=True, ticket_id=t["id"])

    def admin_reply(self, ticket_id, text):
        """Dipanggil petugas. Mengembalikan info kanal untuk pengiriman, atau None."""
        with self._lock:
            t = self.handoff.get(ticket_id)
            if not t or t["status"] != "open":
                return None
            s = self.sessions.peek(t["_sid"])
            if not s:
                return None
            self.handoff.add_message(ticket_id, "admin", text)
            s.outbox.append(text)
            return {"channel": s.channel, "channel_user": s.channel_user}

    def close_ticket(self, ticket_id):
        with self._lock:
            t = self.handoff.close(ticket_id)
            if not t:
                return None
            s = self.sessions.peek(t["_sid"])
            info = {"channel": "web", "channel_user": None}
            if s:
                s.state, s.ticket_id, s.fail_count = "bot", None, 0
                s.outbox.append(self.kb.messages["ticket_closed"])
                info = {"channel": s.channel, "channel_user": s.channel_user}
            return info

    def pop_outbox(self, session_id):
        with self._lock:
            s = self.sessions.peek(session_id)
            if not s or not s.outbox:
                return []
            msgs, s.outbox = s.outbox, []
            return msgs

    # --- internal ---------------------------------------------------------
    def _allow(self, s: Session):
        now = time.time()
        while s.stamps and now - s.stamps[0] > 60:
            s.stamps.popleft()
        if len(s.stamps) >= self.kb.settings["rate_limit_per_minute"]:
            return False
        s.stamps.append(now)
        return True

    def _handoff_mode(self, s: Session, proc: Processed) -> Reply:
        msgs = self.kb.messages
        if len(proc.words) <= 3 and {"bot", "chatbot", "kembali"} & set(proc.words):
            self.handoff.close(s.ticket_id)
            s.state, s.ticket_id, s.fail_count = "bot", None, 0
            return Reply(msgs["back_to_bot"], "back_to_bot")
        self.handoff.add_message(s.ticket_id, "user", proc.raw[:500])
        return Reply(msgs["handoff_waiting"].format(ticket=s.ticket_id), "handoff_waiting",
                     handoff=True, ticket_id=s.ticket_id)


# ======================================================================
# CLI untuk uji cepat
# ======================================================================
def main():
    engine = ChatbotEngine()
    print("=== Chatbot Tumbuhan Tropis (CLI) === ketik 'keluar' untuk berhenti")
    while True:
        try:
            msg = input("Anda : ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if msg.lower() in {"keluar", "exit", "quit"}:
            break
        r = engine.reply("cli-session", msg, channel="cli")
        print(f"Bot  : {r.text}\n       [{r.kind} | conf={r.confidence:.2f}]")


if __name__ == "__main__":
    main()
