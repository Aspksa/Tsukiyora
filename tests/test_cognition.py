"""Cognitive core, opt-in Cloud.ru vectors and local privacy checks.

All provider requests are mocked; no network access, real keys or cloud costs.
"""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cloudru
import launcher
from cognitive_core import CognitiveCore, CognitiveWorker
from memory_store import MemoryStore
from soul_growth import SoulGrowth
from vector_memory import VectorMemory, pack_vector, unpack_vector, similarity


class FakeEmbeddings:
    def __init__(self):
        self.model = "BAAI/bge-m3"
        self.calls = []

    def embedding_model(self):
        return self.model

    def embed(self, texts):
        self.calls.append(list(texts))
        output = []
        for text in texts:
            if "кошка" in text.lower() or "кот" in text.lower():
                output.append([1.0, 0.0, 0.0])
            elif "автомобиль" in text.lower() or "машин" in text.lower():
                output.append([0.0, 1.0, 0.0])
            else:
                output.append([0.0, 0.0, 1.0])
        return output


class VectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "database.sqlite3"
        launcher.init_database(self.path)
        self.storage = MemoryStore(self.path)
        self.index = VectorMemory(self.path)
        self.cloud = FakeEmbeddings()

    def test_opt_in_index_cache_and_retrieval(self):
        cat = self.storage.add_memory("fact", "Моя кошка любит диван")
        car = self.storage.add_memory("fact", "Автомобиль в гараже")
        self.assertEqual(self.index.stats()["indexed_memories"], 0)
        first = self.index.sync(self.cloud)
        self.assertEqual(first["remote_vectors"], 2)
        self.assertEqual(self.index.sync(self.cloud)["processed"], 0)
        self.assertEqual(self.index.stats()["cached_vectors"], 2)
        found = self.index.query("кот", self.cloud)
        self.assertEqual(found["items"][0]["id"], cat["id"])
        self.assertTrue(found["items"][0]["vector_matched"])
        self.assertNotEqual(found["items"][0]["id"], car["id"])
        # Identical queries use the local vector cache, never a second paid request.
        calls = len(self.cloud.calls)
        self.index.query("кот", self.cloud)
        self.assertEqual(len(self.cloud.calls), calls)

    def test_reindex_corrections_and_deleted_memory(self):
        row = self.storage.add_memory("fact", "Автомобиль в гараже")
        self.index.sync(self.cloud)
        self.storage.correct_memory(row["id"], "Моя кошка дома")
        # Stale vectors are not used for changed source text.
        found = self.index.query("автомобиль", self.cloud)
        self.assertFalse(any(x["id"] == row["id"] and x["vector_matched"]
                             for x in found["items"]))
        new = self.index.sync(self.cloud)
        self.assertEqual(new["processed"], 1)
        self.assertEqual(self.index.stats()["indexed_memories"], 1)
        self.storage.delete_memory(row["id"])
        self.index.prune()
        self.assertEqual(self.index.stats()["indexed_memories"], 0)
        self.assertEqual(self.index.query("кошка", self.cloud)["items"], [])

    def test_vector_guards(self):
        with self.assertRaises(ValueError):
            pack_vector([float("nan"), 0.1])
        with self.assertRaises(ValueError):
            pack_vector([0.5])
        with self.assertRaises(ValueError):
            unpack_vector(b"bad", 3)
        self.assertEqual(similarity([1.0, 0.0], [0.0, 1.0]), 0)
        self.assertAlmostEqual(similarity([1.0, 1.0], [1.0, 1.0]), 1.0)


class CognitiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "local.sqlite3"
        launcher.init_database(self.path)
        self.core = CognitiveCore(self.path)
        self.memory = MemoryStore(self.path)

    def test_feedback_changes_safe_style_without_overwriting_persona(self):
        conversation = self.memory.create_conversation()
        answers = self.memory.append_messages(conversation["id"], [
            {"role": "assistant", "content": "Ответ номер " + str(i)}
            for i in range(5)
        ])
        for response in answers[:4]:
            self.core.feedback(response["id"], helpful=False)
        self.assertIn("короче", self.core.guidance())
        self.assertEqual(self.core.status()["feedback"]["total"], 4)
        for response in answers:
            self.core.feedback(response["id"], helpful=True)
        self.assertIn("структуру", self.core.guidance())
        # Existing approved character remains in the original JSON and cannot be overwritten.
        self.assertEqual(launcher.load_personality()["identity"]["name"], "Tsukiyora")
        with self.assertRaises(ValueError):
            self.core.feedback(999999, True)

    def test_background_cycle_biography_and_real_graph(self):
        bio = self.memory.add_memory("biography", "Работаю начальником гаража")
        pref = self.memory.add_memory("preference", "Предпочитаю краткие ответы")
        SoulGrowth(self.path).build_links(pref["id"])
        graph = self.core.graph()
        self.assertEqual({node["id"] for node in graph["nodes"]}, {bio["id"], pref["id"]})
        self.assertFalse(any("api_key" in k for k in graph))
        biography = self.core.biography()
        self.assertEqual(len(biography["approved_history"]), 2)
        result = self.core.cycle()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.core.status()["last_cycle"]["status"], "ok")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        worker = CognitiveWorker(self.core, interval_seconds=30)
        worker.start()
        worker.stop()
        self.assertFalse(worker._thread.is_alive())


class CloudEmbeddingsTests(unittest.TestCase):
    def test_model_discovery_selection_and_response_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            cloud = cloudru.CloudSession(Path(folder) / "models.json")
            cloud.set_key("NOT_REAL_KEY")
            def fake_request(method, resource, key, payload=None):
                self.assertEqual(key, "NOT_REAL_KEY")
                if resource == "models":
                    return {"data": [{"id": "BAAI/bge-m3"}, {"id": "chat/model-1"}]}
                if resource == "embeddings":
                    return {"data": [{"index": i, "embedding": [1.0, float(i + 1), 0.0]}
                                     for i in range(len(payload["input"]))]}
                raise AssertionError(resource)
            with patch.object(cloudru, "_request", side_effect=fake_request):
                cloud.models()
                with self.assertRaises(ValueError):
                    cloud.select_embedding("chat/model-1")
                cloud.select_embedding("BAAI/bge-m3")
                self.assertEqual(cloud.embed(["A", "B"])[1], [1.0, 2.0, 0.0])
            stored = (Path(folder) / "models.json").read_text(encoding="utf-8")
            self.assertIn("BAAI/bge-m3", stored)
            self.assertNotIn("NOT_REAL_KEY", stored)
            with patch.object(cloudru, "_request", return_value={
                    "data": [{"index": 0, "embedding": [float("nan"), 0.0]}]}):
                with self.assertRaises(cloudru.CloudError):
                    cloud.embed(["invalid"])


class CognitiveApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "api.sqlite3"
        launcher.init_database(self.db)
        self.server = launcher.SoulServer(("127.0.0.1", 0), launcher.load_personality(), self.db)
        self.server.cloud.set_key("FAKE_TOKEN")
        self.server.cloud._verified = True
        self.server.cloud._models = ["BAAI/bge-m3", "chat/test"]
        self.server.cloud._model = "chat/test"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.opener = build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, path, value):
        req = Request(self.url + path,
                      data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Origin": self.url},
                      method="POST")
        with self.opener.open(req, timeout=5) as result:
            return json.load(result)

    def get(self, path):
        with self.opener.open(self.url + path, timeout=5) as result:
            return json.load(result)

    def test_no_unauthorized_embedding_and_local_state(self):
        MemoryStore(self.db).add_memory("fact", "Приватное воспоминание")
        self.assertEqual(len(self.get("/api/soul/graph")["nodes"]), 1)
        self.assertEqual(len(self.get("/api/soul/biography")["approved_history"]), 0)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/vectors/index", {"limit": 1})
        self.assertEqual(err.exception.code, 400)
        self.assertEqual(self.get("/api/soul/cognition")["vectors"]["cached_vectors"], 0)
        result = self.post("/api/cloud/embedding/model", {"model": "BAAI/bge-m3"})
        self.assertEqual(result["embedding_model"], "BAAI/bge-m3")
        with patch.object(self.server.cloud, "embed", return_value=[[1.0, 0.0, 0.0]]) as embed:
            result = self.post("/api/soul/vectors/index",
                               {"limit": 1, "confirm_send_to_cloud": True})
            self.assertEqual(result["remote_vectors"], 1)
            embed.assert_called_once()
        self.assertEqual(self.get("/api/soul/cognition")["vectors"]["indexed_memories"], 1)


if __name__ == "__main__":
    unittest.main()
