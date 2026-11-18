"""Fully local conversation search and source-linked extractive recaps.

No model calls, text leaves the SQLite database only when explicitly requested
by the local browser. These are excerpts, never fabricated LLM summaries.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3

MIN_SUMMARY_MESSAGES = 12


class ConversationMemory:
    def __init__(self, path: Path):
        self.path=Path(path)

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as db:
            with db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS conversation_recaps("
                    "conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),"
                    "source_max_id INTEGER NOT NULL,source_count INTEGER NOT NULL,"
                    "summary TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            exists=db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='conversation_message_fts' "
                "AND type='table'"
            ).fetchone() is not None
            try:
                with db:
                    db.executescript("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS conversation_message_fts
                          USING fts5(content,content='messages',content_rowid='id',
                                     tokenize='unicode61');
                        CREATE TRIGGER IF NOT EXISTS conversation_fts_ai
                          AFTER INSERT ON messages BEGIN
                            INSERT INTO conversation_message_fts(rowid,content)
                            VALUES(new.id,new.content);
                        END;
                        CREATE TRIGGER IF NOT EXISTS conversation_fts_ad
                          AFTER DELETE ON messages BEGIN
                            INSERT INTO conversation_message_fts(
                              conversation_message_fts,rowid,content)
                            VALUES('delete',old.id,old.content);
                        END;
                        CREATE TRIGGER IF NOT EXISTS conversation_fts_au
                          AFTER UPDATE OF content ON messages BEGIN
                            INSERT INTO conversation_message_fts(
                              conversation_message_fts,rowid,content)
                            VALUES('delete',old.id,old.content);
                            INSERT INTO conversation_message_fts(rowid,content)
                            VALUES(new.id,new.content);
                        END;
                    """)
                    if not exists:
                        db.execute(
                            "INSERT INTO conversation_message_fts(conversation_message_fts)"
                            " VALUES('rebuild')"
                        )
            except sqlite3.OperationalError as error:
                # Python may have SQLite built without FTS5. Full scan fallback
                # remains bounded in return size; no hidden model dependency.
                if "fts5" not in str(error).lower() and "no such module" not in str(error).lower():
                    raise

    def _fts_available(self,db)->bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='conversation_message_fts'"
        ).fetchone() is not None

    def search(self,query:str,limit:int=20,before_id:int|None=None)->dict:
        if not isinstance(query,str) or not 2<=len(query.strip())<=250:
            raise ValueError("Текст поиска должен содержать от 2 до 250 символов.")
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError("Invalid history search page limit.")
        if before_id is not None and (type(before_id) is not int or before_id<1):
            raise ValueError("Invalid history cursor.")
        tokens=list(dict.fromkeys(re.findall(r"\w{2,}",query.casefold(),flags=re.UNICODE)))[:12]
        if not tokens:
            raise ValueError("Поиск требует хотя бы одно слово.")
        with closing(self.connect()) as db:
            if self._fts_available(db):
                # Escaped, quoted tokens are passed as bound MATCH values; no
                # query syntax from the user is executed as FTS operators.
                match=" OR ".join('"' + word.replace('"','""') + '"' for word in tokens)
                rows=db.execute(
                    "SELECT m.id,m.conversation_id,c.title,m.role,"
                    "substr(m.content,1,300) AS excerpt,m.created_at "
                    "FROM conversation_message_fts f "
                    "JOIN messages m ON m.id=f.rowid "
                    "JOIN conversations c ON c.id=m.conversation_id "
                    "WHERE conversation_message_fts MATCH ? "
                    "AND (? IS NULL OR m.id<?) "
                    "ORDER BY m.id DESC LIMIT ?",
                    (match,before_id,before_id,limit+1)
                ).fetchall()
                mode="fts5"
            else:
                rows=db.execute(
                    "SELECT m.id,m.conversation_id,c.title,m.role,"
                    "substr(m.content,1,300) AS excerpt,m.created_at "
                    "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                    "WHERE ("+" OR ".join("instr(lower(m.content),?)>0" for _ in tokens)+") "
                    "AND (? IS NULL OR m.id<?) ORDER BY m.id DESC LIMIT ?",
                    (*tokens,before_id,before_id,limit+1)
                ).fetchall()
                mode="local_text"
        result=[dict(x) for x in rows[:limit]]
        return {"items":result,"has_more":len(rows)>limit,
                "next_before_id":result[-1]["id"] if len(rows)>limit else None,
                "mode":mode,"cloud_requests":0,"private_local_only":True}

    def summary(self,conversation_id:str)->dict:
        if not isinstance(conversation_id,str) or not 1<=len(conversation_id)<=64:
            raise ValueError("Invalid conversation.")
        with closing(self.connect()) as db:
            title=db.execute("SELECT title FROM conversations WHERE id=?",
                             (conversation_id,)).fetchone()
            if title is None:raise ValueError("Диалог не существует.")
            aggregate=db.execute(
                "SELECT COUNT(*) AS n,COALESCE(MAX(id),0) AS max_id FROM messages "
                "WHERE conversation_id=?",(conversation_id,)
            ).fetchone()
            count,max_id=aggregate["n"],aggregate["max_id"]
            if count<MIN_SUMMARY_MESSAGES:
                return {"conversation_id":conversation_id,"message_count":count,
                        "ready":False,"required":MIN_SUMMARY_MESSAGES,
                        "summary":"","sources":[],"cloud_requests":0,
                        "method":"local_source_excerpts"}
            cached=db.execute(
                "SELECT source_max_id,source_count,summary "
                "FROM conversation_recaps WHERE conversation_id=?",(conversation_id,)
            ).fetchone()
            # Source locations are returned every time and remain anchored to
            # original IDs; a cached summary is only valid for identical count/id.
            if cached and cached["source_max_id"]==max_id and cached["source_count"]==count:
                snippets=self._selected(db,conversation_id)
                return self._result(conversation_id,count,cached["summary"],snippets)
            snippets=self._selected(db,conversation_id)
            lines=[]
            for message in snippets:
                speaker="Вопрос" if message["role"]=="user" else "Ответ"
                excerpt=" ".join(message["content"].split())[:145]
                lines.append(f"{speaker} [Сообщение #{message['id']}]: {excerpt}")
            summary="\n".join(lines)[:1700]
            with db:
                db.execute(
                    "INSERT INTO conversation_recaps(conversation_id,source_max_id,"
                    "source_count,summary) VALUES(?,?,?,?) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET "
                    "source_max_id=excluded.source_max_id,"
                    "source_count=excluded.source_count,summary=excluded.summary,"
                    "updated_at=CURRENT_TIMESTAMP",
                    (conversation_id,max_id,count,summary)
                )
            return self._result(conversation_id,count,summary,snippets)

    def _selected(self,db,conversation_id):
        first=db.execute(
            "SELECT id,role,content,created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id LIMIT 2",(conversation_id,)
        ).fetchall()
        last=db.execute(
            "SELECT id,role,content,created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id DESC LIMIT 8",(conversation_id,)
        ).fetchall()
        unique={x["id"]:dict(x) for x in (*first,*last)}
        return sorted(unique.values(),key=lambda x:x["id"])

    @staticmethod
    def _result(conversation_id,count,summary,snippets):
        return {"conversation_id":conversation_id,"message_count":count,
                "ready":True,"summary":summary,
                "sources":[{"message_id":m["id"],"role":m["role"],
                            "created_at":m["created_at"]} for m in snippets],
                "method":"local_source_excerpts",
                "not_an_llm_summary":True,"cloud_requests":0}

    def health(self)->dict:
        with closing(self.connect()) as db:
            fts=self._fts_available(db)
            summary_count=db.execute("SELECT COUNT(*) FROM conversation_recaps").fetchone()[0]
            return {"fts5":fts,"cached_recaps":summary_count,
                    "index_integrity_unknown":True,
                    "cloud_requests":0,"local_only":True}
