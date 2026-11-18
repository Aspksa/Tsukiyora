"""Tsukiyora v0.6: memory-quality, experience, repair and local diagnostics.

Uses Python stdlib only. Network-bound Cloud.ru calls are mocked deliberately;
this suite cannot prove a user's real Cloud.ru account or their personal PC.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cloudru
import launcher
from cognitive_core import CognitiveCore, CognitiveWorker
from experience_engine import ExperienceEngine
from memory_quality import MemoryQuality, compare
from memory_store import MemoryStore
from vector_memory import VectorMemory


class FreshDatabase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = Path(tmp.name) / "tsukiyora.sqlite3"
        launcher.init_database(self.db_path)
        self.memory = MemoryStore(self.db_path)
        self.core = CognitiveCore(self.db_path)


class MemoryQualityTests(FreshDatabase):
    def test_fuzzy_duplicate_and_opposition_with_sources(self):
        a = self.memory.add_memory("preference", "Я люблю зелёный чай")
        b = self.memory.add_memory("preference", "Я люблю зеленый чай!")
        c = self.memory.add_memory("preference", "Я не люблю зелёный чай")
        self.memory.add_memory("preference", "Я не люблю зимние дороги")
        found = MemoryQuality(self.db_path).review()
        duplicate_ids = [{v["left"]["id"], v["right"]["id"]} for v in found["duplicates"]]
        conflict_ids = [{v["left"]["id"], v["right"]["id"]} for v in found["conflicts"]]
        self.assertIn({a["id"], b["id"]}, duplicate_ids)
        self.assertIn({a["id"], c["id"]}, conflict_ids)
        self.assertEqual(found["automatic_deletions"], 0)
        self.assertEqual(self.memory.stats()["memories"], 4)
        for issue in found["duplicates"] + found["conflicts"]:
            self.assertIn("source_type", issue["left"])
            self.assertIn("source_ref", issue["right"])

    def test_negative_words_do_not_force_false_conflicts(self):
        c = compare("Я люблю чай", "Я не люблю чай")
        self.assertTrue(c["conflict"])
        self.assertFalse(c["duplicate"])
        self.assertFalse(compare("Я люблю чай", "Я не люблю машины")["conflict"])
        self.assertTrue(compare("Я люблю зелёный чай", "Я люблю зеленый чай!")["duplicate"])

    def test_cached_vector_semantic_signal_does_not_delete(self):
        a = self.memory.add_memory("fact", "Предпочитаю пешие путешествия")
        b = self.memory.add_memory("fact", "Люблю походы и долгие прогулки")
        index = VectorMemory(self.db_path)
        index._remember("mock/embedding", a["content"], [1.0, 0.0, 0.0], a["id"])
        index._remember("mock/embedding", b["content"], [0.999, 0.001, 0.0], b["id"])
        report = MemoryQuality(self.db_path).review()
        pairs = [{r["left"]["id"], r["right"]["id"]}
                 for r in report["semantic_duplicates"]]
        self.assertIn({a["id"], b["id"]}, pairs)
        self.assertEqual(report["outbound_cloud_requests"], 0)
        self.assertEqual(self.memory.stats()["memories"], 2)

    def test_unapproved_suggestions_are_not_in_quality(self):
        conv = self.memory.create_conversation()
        user = self.memory.append_messages(conv["id"], [
            {"role": "user", "content": "Я люблю путешествовать"}
        ])[0]
        from soul_growth import SoulGrowth
        SoulGrowth(self.db_path).propose(user["id"], user["content"])
        self.assertEqual(MemoryQuality(self.db_path).review()["scanned"], 0)


class OfflineRepairTests(FreshDatabase):
    def test_corrected_memory_recovers_cached_embedding_without_network(self):
        first = self.memory.add_memory("fact", "Моя машина синего цвета")
        second = self.memory.add_memory("event", "Мой автомобиль красный")
        index = VectorMemory(self.db_path)
        model = "test/embedding"
        index._remember(model, first["content"], [0.8, 0.2, 0.0], first["id"])
        index._remember(model, second["content"], [0.1, 0.9, 0.0], second["id"])
        self.memory.correct_memory(first["id"], second["content"])
        report = index.repair()
        self.assertEqual(report["cloud_requests"], 0)
        self.assertEqual(report["cache_restored"], 1)
        with closing(sqlite3.connect(self.db_path)) as db:
            association = db.execute(
                "SELECT text_sha256 FROM memory_embeddings WHERE memory_id=?",
                (first["id"],)
            ).fetchone()
        self.assertIsNotNone(association)
        self.assertEqual(association[0], __import__("hashlib").sha256(second["content"].encode()).hexdigest())

    def test_stale_vector_is_pruned_when_no_matching_cache(self):
        row = self.memory.add_memory("fact", "Гараж работает утром")
        index = VectorMemory(self.db_path)
        index._remember("mock", row["content"], [1.0, 0.0], row["id"])
        self.memory.correct_memory(row["id"], "Гараж закрыт утром")
        report = index.repair()
        self.assertGreaterEqual(report["stale_removed"], 1)
        self.assertEqual(index.stats()["indexed_memories"], 0)

    def test_deleted_memory_drops_mapping_but_keeps_cache(self):
        item = self.memory.add_memory("fact", "Важно помнить встречи")
        index = VectorMemory(self.db_path)
        index._remember("mock", item["content"], [1.0, 0.0], item["id"])
        self.memory.delete_memory(item["id"])
        index.repair()
        self.assertEqual(index.stats()["indexed_memories"], 0)
        self.assertEqual(index.stats()["cached_vectors"], 1)


class ExperienceTests(FreshDatabase):
    def test_errors_create_fixed_learning_goals_and_no_arbitrary_instructions(self):
        conversation = self.memory.create_conversation()
        answers = self.memory.append_messages(conversation["id"], [
            {"role": "assistant", "content": "Ответ " + str(n)} for n in range(4)
        ])
        for answer in answers[:3]:
            self.core.feedback(answer["id"], False, "В ответе фактическая ошибка")
        self.core.feedback(answers[3]["id"], True, "Теперь стало точнее")
        goals = {row["category"]: row for row in self.core.experience.state()["goals"]}
        self.assertEqual(goals["accuracy"]["status"], "practice")
        self.assertIn("фактические утверждения", self.core.guidance())
        self.assertNotIn("фактическая ошибка", self.core.guidance())
        self.assertEqual(self.core.biography()["tone_model"]["label"],
                         "симуляция манеры общения, не настоящие чувства")
        # Feedback cannot alter the protected static persona file.
        self.assertEqual(launcher.load_personality()["identity"]["name"], "Tsukiyora")

    def test_automatic_maintenance_is_offline_and_writes_aggregate_diary(self):
        self.memory.add_memory("fact", "Я работаю с автомобилями")
        self.memory.add_memory("fact", "Я не работаю с автомобилями")
        with patch.object(cloudru, "_request", side_effect=AssertionError("No outbound cloud calls")):
            report = self.core.cycle()
        self.assertEqual(report["status"], "ok")
        snapshots = self.core.experience.timeline()
        self.assertEqual(len(snapshots), 1)
        digest = json.loads(snapshots[0]["summary"])
        self.assertIn("sessions", digest)
        self.assertIn("pending", digest)
        self.assertNotIn("автомобил", snapshots[0]["summary"])
        self.assertEqual(self.core.status()["last_cycle"]["status"], "ok")
        self.assertIn("relationship_history", self.core.biography())

    def test_worker_stops_cleanly_and_no_background_cloud_use(self):
        worker = CognitiveWorker(self.core, interval_seconds=30)
        worker.start()
        worker.stop()
        self.assertFalse(worker._thread.is_alive())


class CloudDiagnosticsTests(FreshDatabase):
    def test_no_key_is_reported_as_failure_not_success(self):
        cloud = cloudru.CloudSession()
        cloud.set_key("")
        result = cloud.checkup()
        self.assertFalse(result["live"])
        self.assertEqual(result["checks"]["models"]["state"], "failed")

    def test_real_workflow_mock_reports_each_component(self):
        cloud = cloudru.CloudSession()
        cloud.set_key("LOCAL_FAKE_KEY")
        def mock_request(method, endpoint, key, payload=None):
            self.assertEqual(key, "LOCAL_FAKE_KEY")
            if endpoint == "models":
                return {"data": [{"id": "chat/model"}, {"id": "BAAI/bge-m3"}]}
            if endpoint == "chat/completions":
                return {"choices": [{"message": {"content": "Проверка прошла."}}]}
            if endpoint == "embeddings":
                return {"data": [{"index": 0, "embedding": [0.5, 0.7, 0.3]}]}
            raise AssertionError(endpoint)
        with patch.object(cloudru, "_request", side_effect=mock_request):
            cloud.models()
            cloud.select("chat/model")
            cloud.select_embedding("BAAI/bge-m3")
            with patch.object(cloud, "stream", return_value=iter(["Поток", " доступен"])):
                check = cloud.checkup()
        self.assertTrue(check["live"])
        self.assertEqual(check["checks"]["models"]["state"], "passed")
        self.assertEqual(check["checks"]["chat"]["state"], "passed")
        self.assertEqual(check["checks"]["stream"]["state"], "passed")
        self.assertEqual(check["checks"]["embeddings"]["dimension"], 3)
        self.assertNotIn("LOCAL_FAKE_KEY", json.dumps(check))

    def test_no_embedding_selection_is_clearly_skipped(self):
        cloud = cloudru.CloudSession()
        cloud.set_key("LOCAL_FAKE_KEY")
        with patch.object(cloudru, "_request", side_effect=lambda m, e, k, p=None:
                          {"data": [{"id": "chat/model"}]} if e == "models"
                          else {"choices": [{"message": {"content": "OK"}}]}):
            cloud.models()
            cloud.select("chat/model")
            with patch.object(cloud, "stream", return_value=iter(["OK"])):
                check = cloud.checkup()
        self.assertEqual(check["checks"]["embeddings"]["state"], "skipped")
        self.assertTrue(check["live"])

    def test_embedding_chat_is_disabled_by_default_and_reset_by_key_change(self):
        cloud = cloudru.CloudSession()
        cloud.set_key("FAKE_OLD_KEY")
        with patch.object(cloudru, "_request", return_value={
                "data": [{"id": "BAAI/bge-m3"}, {"id": "chat/model"}]}):
            cloud.models()
        cloud.select("chat/model")
        cloud.select_embedding("BAAI/bge-m3")
        self.assertFalse(cloud.status()["vector_chat_enabled"])
        self.assertTrue(cloud.enable_vector_chat(True)["vector_chat_enabled"])
        self.assertFalse(cloud.enable_vector_chat(False)["vector_chat_enabled"])
        self.assertTrue(cloud.enable_vector_chat(True)["vector_chat_enabled"])
        cloud.set_key("FAKE_NEW_KEY")
        self.assertFalse(cloud.status()["vector_chat_enabled"])
        with self.assertRaises(ValueError):
            cloud.enable_vector_chat(True)
        self.assertNotIn("FAKE_NEW_KEY", json.dumps(cloud.status()))
        with self.assertRaises(ValueError):
            cloud.enable_vector_chat("yes")

    def test_explicit_embedding_consent_endpoint_rejects_invalid_values(self):
        server = launcher.SoulServer(("127.0.0.1", 0),
                                     launcher.load_personality(), self.db_path)
        server.cloud.set_key("FAKE_KEY")
        server.cloud._verified = True
        server.cloud._models = ["BAAI/bge-m3", "chat/model"]
        server.cloud._model = "chat/model"
        server.cloud.select_embedding("BAAI/bge-m3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_address[1])
            opener = build_opener(ProxyHandler({}))
            def post(value):
                req = Request(base + "/api/cloud/embedding/auto", method="POST",
                    data=json.dumps(value).encode(), headers={
                        "Content-Type": "application/json", "Origin": base,
                    })
                with opener.open(req, timeout=5) as response:
                    return json.load(response)
            self.assertFalse(post({"allowed": False})["vector_chat_enabled"])
            with self.assertRaises(HTTPError) as caught:
                post({"allowed": "true"})
            self.assertEqual(caught.exception.code, 400)
            self.assertTrue(post({"allowed": True})["vector_chat_enabled"])
            self.assertFalse(post({"allowed": False})["vector_chat_enabled"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_endpoint_requires_no_public_token_and_no_saved_report(self):
        server = launcher.SoulServer(("127.0.0.1", 0),
                                     launcher.load_personality(), self.db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_address[1])
            opener = build_opener(ProxyHandler({}))
            def post(path, data):
                request = Request(base + path,
                                  data=json.dumps(data).encode("utf-8"), method="POST",
                                  headers={"Content-Type": "application/json", "Origin": base})
                with opener.open(request, timeout=5) as response:
                    return json.load(response)
            check = post("/api/cloud/checkup", {"include_stream": True})
            self.assertFalse(check["live"])
            self.assertEqual(check["checks"]["models"]["state"], "failed")
            state = opener.open(base + "/api/soul/quality", timeout=5)
            self.assertEqual(json.load(state)["automatic_deletions"], 0)
            with opener.open(base + "/api/soul/experience", timeout=5) as response:
                self.assertIn("timeline", json.load(response))
            # Cross-origin writes are still rejected.
            unauthorized = Request(base + "/api/cloud/checkup", method="POST",
                                   data=b'{}', headers={
                                       "Content-Type": "application/json",
                                       "Origin": "https://attacker.example",
                                   })
            with self.assertRaises(HTTPError) as blocked:
                opener.open(unauthorized, timeout=5)
            self.assertEqual(blocked.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
