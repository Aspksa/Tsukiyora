"""Opt-in real one-million-row SQLite atlas load check, no Cloud.ru.

Run with python scripts/test_million_atlas.py. Measures server-side SQLite
queries and bounded JSON payload, NOT Canvas FPS on the user's computer.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from memory_store import MemoryStore
from soul_growth import SoulGrowth
from brain_atlas import BrainAtlas
from atlas_materializer import AtlasMaterializer

COUNT=1_000_000

with tempfile.TemporaryDirectory(prefix="tsukiyora-million-") as folder:
    dbfile=Path(folder)/"million.sqlite3"
    store=MemoryStore(dbfile)
    store.initialize()
    SoulGrowth(dbfile).initialize()
    begin=time.monotonic()
    # Build exactly one million ACTUAL memory_item rows, in a single
    # transaction. No synthetic client-side placeholders.
    with closing(store.connect()) as db,db:
        db.executescript("""
            WITH RECURSIVE sequence(n) AS (
                SELECT 0 UNION ALL SELECT n+1 FROM sequence WHERE n<999
            )
            INSERT INTO memory_items(kind,content,normalized_text,fingerprint,
                                     source_type,verification_state)
            SELECT CASE (a.n % 4) WHEN 0 THEN 'fact'
                  WHEN 1 THEN 'event'
                  WHEN 2 THEN 'preference' ELSE 'biography' END,
                  'Тестовая запись '||(a.n*1000+b.n+1),
                  'тестовая запись '||(a.n*1000+b.n+1),
                  printf('%064d',a.n*1000+b.n+1),
                  'manual','user_reported'
            FROM sequence AS a CROSS JOIN sequence AS b;
        """)
        count=db.execute(
            "SELECT COUNT(*) FROM memory_items WHERE status='active'"
        ).fetchone()[0]
    assert count==COUNT, f"Only {count} rows inserted"
    inserted=time.monotonic()-begin
    materializer=AtlasMaterializer(dbfile)
    materializer.initialize()
    cache=materializer.verify()
    assert cache["verified"] and cache["cached_count"]==COUNT
    atlas=BrainAtlas(dbfile)
    start=time.monotonic()
    view=atlas.overview()
    overview_s=time.monotonic()-start
    assert view["total"]==COUNT
    assert view["materialized"]
    assert sum(x["count"] for x in view["sectors"])==COUNT
    assert len(view["sectors"])==8
    for sector in view["sectors"]:
        start=time.monotonic()
        top=atlas.regions(sector["id"])
        assert len(top["regions"])<=100
        assert top["total"]==sector["count"]
        assert time.monotonic()-start<25
    first=view["sectors"][0]
    regions=atlas.regions(first["id"])
    assert regions["regions"]
    region=regions["regions"][0]["region"]
    page=atlas.regions(first["id"],cursor=region)
    assert all(x["region"]<region for x in page["regions"])
    groups=atlas.clusters(first["id"],region=region)
    assert groups["clusters"]
    bucket=groups["clusters"][0]["bucket"]
    nodes=atlas.nodes(first["id"],bucket)
    assert len(nodes["nodes"])<=160
    assert len(json.dumps(nodes,ensure_ascii=False).encode("utf-8"))<80000
    target=atlas.locate(COUNT)
    assert target["memory_id"]==COUNT
    assert target["region"]==(COUNT-1)//8192
    assert target["bucket"]==(COUNT-1)//256
    last=atlas.nodes(target["sector"],target["bucket"])
    assert any(x["id"]==COUNT for x in last["nodes"])
    print(json.dumps({
        "status":"passed","actual_sqlite_memories":count,
        "insert_seconds":round(inserted,2),
        "overview_query_seconds":round(overview_s,3),
        "sectors":len(view["sectors"]),
        "materialized_regions":materializer.status()["regions"],
        "materialized_counts_verified":cache["verified"],
        "max_nodes_in_detail":160,
        "cloud_calls":0,
        "user_browser_fps_verified":False,
    }))
