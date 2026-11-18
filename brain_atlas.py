"""SQLite-backed progressive brain atlas for tens of thousands of memories.

Overview -> selected sector -> 256-ID cluster -> bounded real nodes.
Only the requested level is transmitted; no fabricated neurons or edges.
No new dependencies, external API calls, hidden data changes, or N^2 scans.
"""
from __future__ import annotations

from contextlib import closing
from memory_store import MemoryStore

KINDS = ("fact", "preference", "event", "biography")
KIND_BASE = {"fact":0,"preference":2,"event":4,"biography":6}
BUCKET_SIZE = 256
MAX_BUCKETS = 240
MAX_NODES = 160
REGION_SPAN = BUCKET_SIZE * 32
MAX_REGIONS = 100


def decode_sector(sector: int) -> tuple[str,int]:
    if type(sector) is not int or not 0 <= sector <= 7:
        raise ValueError("Сектор должен быть от 0 до 7.")
    return KINDS[sector//2],sector%2


class BrainAtlas:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def connect(self):
        return self.store.connect()

    def overview(self) -> dict:
        with closing(self.connect()) as db:
            data = db.execute(
                "SELECT kind,(id % 2) AS parity,COUNT(*) n,MAX(id) last_id "
                "FROM memory_items WHERE status='active' "
                "GROUP BY kind,(id % 2)"
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM memory_items WHERE status='active'"
            ).fetchone()[0]
            edge_count = db.execute(
                "SELECT COUNT(*) FROM memory_links l "
                "JOIN memory_items a ON a.id=l.source_id AND a.status='active' "
                "JOIN memory_items b ON b.id=l.target_id AND b.status='active'"
            ).fetchone()[0]
        sectors = sorted([
            {"id":KIND_BASE[x["kind"]]+x["parity"],"kind":x["kind"],
             "count":x["n"],"newest_id":x["last_id"]}
            for x in data
        ],key=lambda x:x["id"])
        return {"level":"overview","total":total,"sectors":sectors,
                "connections":edge_count,
                "page_size":BUCKET_SIZE,"region_span":REGION_SPAN,"hierarchical":True,
                "maximum_records_in_response":MAX_BUCKETS}

    def regions(self, sector: int, cursor: int | None = None) -> dict:
        """Hierarchical, seek-paginated 8192-ID regions for 100k+ memories."""
        kind, parity = decode_sector(sector)
        if cursor is not None and (type(cursor) is not int or cursor < 0):
            raise ValueError("Invalid region cursor.")
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT CAST((id-1)/? AS INTEGER) region, COUNT(*) n,"
                "MIN(id) first_id,MAX(id) last_id FROM memory_items "
                "WHERE status='active' AND kind=? AND (id%2)=? "
                "AND (? IS NULL OR id<?) "
                "GROUP BY region ORDER BY region DESC LIMIT ?",
                (REGION_SPAN, kind, parity, cursor, cursor*REGION_SPAN+1
                 if cursor is not None else None, MAX_REGIONS+1)
            ).fetchall()
            total=db.execute(
                "SELECT COUNT(*) FROM memory_items "
                "WHERE status='active' AND kind=? AND (id%2)=?",
                (kind,parity)
            ).fetchone()[0]
        more=len(rows)>MAX_REGIONS
        visible=rows[:MAX_REGIONS]
        return {"level":"regions","sector":sector,"kind":kind,"total":total,
                "regions":[{"region":x["region"],"count":x["n"],
                            "first_id":x["first_id"],"last_id":x["last_id"]}
                           for x in visible],
                "next_cursor":visible[-1]["region"] if more else None,
                "has_more":more,"region_span":REGION_SPAN,
                "bounded":True}

    def clusters(self, sector: int, cursor: int | None = None, region: int | None = None) -> dict:
        kind, parity = decode_sector(sector)
        if cursor is not None and (type(cursor) is not int or cursor < 0):
            raise ValueError("Неверная позиция списка кластеров.")
        if region is not None and (type(region) is not int or region < 0):
            raise ValueError("Неверная область памяти.")
        with closing(self.connect()) as db:
            query = (
                "SELECT CAST((id-1)/? AS INTEGER) bucket,COUNT(*) n,"
                "MIN(id) first_id,MAX(id) last_id "
                "FROM memory_items WHERE status='active' AND kind=? AND (id%2)=?"
                " AND (? IS NULL OR CAST((id-1)/? AS INTEGER)<?)"
                " AND (? IS NULL OR id BETWEEN ? AND ?)"
                " GROUP BY bucket ORDER BY bucket DESC LIMIT ?"
            )
            low = region*REGION_SPAN+1 if region is not None else 0
            high = (region+1)*REGION_SPAN if region is not None else 0
            rows = db.execute(
                query,(BUCKET_SIZE,kind,parity,cursor,BUCKET_SIZE,cursor,
                       region,low,high,MAX_BUCKETS+1)
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM memory_items WHERE status='active' "
                "AND kind=? AND (id%2)=? AND (? IS NULL OR id BETWEEN ? AND ?)",
                (kind,parity,region,low,high)
            ).fetchone()[0]
        more = len(rows)>MAX_BUCKETS
        items = rows[:MAX_BUCKETS]
        return {"level":"clusters","sector":sector,"kind":kind,"region":region,"total":total,
                "clusters":[
                    {"bucket":x["bucket"],"count":x["n"],
                     "first_id":x["first_id"],"last_id":x["last_id"]}
                    for x in items],
                "next_cursor":items[-1]["bucket"] if more else None,
                "has_more":more,"bucket_size":BUCKET_SIZE}

    def nodes(self, sector: int, bucket: int) -> dict:
        kind,parity=decode_sector(sector)
        if type(bucket) is not int or not 0 <= bucket <= 10_000_000:
            raise ValueError("Неверная группа памяти.")
        low=bucket*BUCKET_SIZE+1
        high=(bucket+1)*BUCKET_SIZE
        with closing(self.connect()) as db:
            nodes=[dict(x) for x in db.execute(
                "SELECT id,kind,substr(content,1,180) AS preview,source_type,"
                "source_ref,created_at FROM memory_items "
                "WHERE status='active' AND kind=? AND (id%2)=? "
                "AND id BETWEEN ? AND ? ORDER BY id DESC LIMIT ?",
                (kind,parity,low,high,MAX_NODES)
            )]
            ids={x["id"] for x in nodes}
            # Query only edges for this tiny ID window. Endpoints must both
            # exist in the active visible cluster to prevent stale links.
            edges=[dict(x) for x in db.execute(
                "SELECT l.source_id,l.target_id,l.relation FROM memory_links l "
                "JOIN memory_items a ON a.id=l.source_id AND a.status='active' "
                "JOIN memory_items b ON b.id=l.target_id AND b.status='active' "
                "WHERE (l.source_id BETWEEN ? AND ? OR "
                "l.target_id BETWEEN ? AND ?) LIMIT 600",
                (low,high,low,high)
            )]
        return {"level":"nodes","sector":sector,"bucket":bucket,"kind":kind,
                "nodes":nodes,
                "edges":[x for x in edges if x["source_id"] in ids and x["target_id"] in ids],
                "count":len(nodes),"bounded":True,
                "pagination_not_needed":len(nodes)<=MAX_NODES}
