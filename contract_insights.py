"""Local, rule-based highlights from user-approved contractual text.

All hits are candidate strings from actual source fragments; this is not a
legal interpretation, a verified obligation, or an LLM-generated assertion.
"""
from __future__ import annotations
from contextlib import closing
import re

from memory_store import MemoryStore

DATES = re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[012])[./-](?:19|20)\d{2}\b")
MONEY = re.compile(
    r"(?<!\w)(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?\s*"
    r"(?:руб(?:лей|ля|ль|\.)?|₽|RUB|евро|EUR|USD|доллар(?:ов|а)?)\b",re.I)
CLAUSES = re.compile(
    r"\b(?:обязу(?:ется|ются)|долж(?:ен|на|ны)|необходимо|"
    r"ответственност[ьи]|неустойк[аи]|штраф(?:ы|а)?|"
    r"оплат[ауы]|поставк[ауы]|срок(?:и|а|ом)?|"
    r"расторжен(?:ие|ия)|сторон[аы]|исполнен(?:ие|ия))\b",re.I)
MAX_HITS=90


class ContractInsights:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def inspect(self,document_id:int,limit:int=60)->dict:
        if type(document_id) is not int or document_id<=0 or type(limit) is not int or not 1<=limit<=90:
            raise ValueError("Invalid document ID or limit.")
        with closing(self.store.connect()) as db:
            doc=db.execute(
                "SELECT id,name,revision FROM source_documents WHERE id=?",
                (document_id,)
            ).fetchone()
            if doc is None:raise ValueError("Документ не найден.")
            rows=db.execute(
                "SELECT ordinal,content FROM document_chunks WHERE document_id=? "
                "ORDER BY ordinal LIMIT 100",(document_id,)
            ).fetchall()
            # Compare only other local document snippets, without disclosing
            # unrelated source text to any cloud provider.
            others=db.execute(
                "SELECT d.id,d.name,c.ordinal,c.content FROM document_chunks c "
                "JOIN source_documents d ON d.id=c.document_id "
                "WHERE d.id<>? ORDER BY c.id DESC LIMIT 200",(document_id,)
            ).fetchall()
        items=[]
        for row in rows:
            for line in re.split(r"(?<=[.;!?])\s+|\n+",row["content"]):
                if not line.strip():continue
                tags=[]
                if DATES.search(line):tags.append("date")
                if MONEY.search(line):tags.append("amount")
                if CLAUSES.search(line):tags.append("contract_clause")
                if not tags:continue
                items.append({"fragment":row["ordinal"],"excerpt":line[:350],
                              "types":tags,
                              "citation":f"[Документ #{document_id}, фрагмент {row['ordinal']}]",
                              "source_verified":True,"legal_meaning_verified":False})
                if len(items)>=limit:break
            if len(items)>=limit:break
        # Only candidate *differences* for same named documents or some
        # matching context. Avoid calling these logically incompatible terms.
        observed=set()
        differences=[]
        for item in items:
            if "amount" not in item["types"]:continue
            amounts={x.group(0).strip().casefold() for x in MONEY.finditer(item["excerpt"])}
            for other in others:
                alt={x.group(0).strip().casefold() for x in MONEY.finditer(other["content"])}
                if not alt or alt==amounts:continue
                pair=(other["id"],item["fragment"],other["ordinal"])
                if pair in observed:continue
                observed.add(pair)
                differences.append({
                    "type":"different_amounts_not_proven_conflict",
                    "first":item["citation"],
                    "second":f"[Документ #{other['id']}, фрагмент {other['ordinal']}]",
                    "note":"Разные суммы не обязательно противоречат: нужна проверка условий."
                })
                if len(differences)>=8:break
            if len(differences)>=8:break
        return {"document_id":doc["id"],"name":doc["name"],
                "revision":doc["revision"],"items":items,
                "possible_differences":differences,
                "method":"local_regular_expressions",
                "expert_legal_review_required":True,
                "cloud_requests":0}
