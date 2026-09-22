"""Focused regression tests for live faults and persisted rollbacks."""

import os
import tempfile
import time
import unittest

import api
import tracer


class LiveFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        tracer.set_db_path(os.path.join(self.temp_dir.name, "timequake-test.db"))
        tracer.init_default_db()
        self.client = api.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _record(trace_id, span_id, parent_span_id, service, start_time, duration_ms, fault=False):
        tracer.record_service_span({
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "service": service,
            "start_time": start_time,
            "end_time": start_time + duration_ms / 1000,
            "duration_ms": duration_ms,
            "status": "ok",
            "is_fault_run": fault,
        })

    def _seed_trace(self, trace_id, start_time, payment_duration, fault=False):
        # Auth and cart each retain 30ms self-time; payment owns the injected delay.
        self._record(trace_id, f"{trace_id}-auth", "", "auth", start_time, payment_duration + 60, fault)
        self._record(trace_id, f"{trace_id}-cart", f"{trace_id}-auth", "cart", start_time + 0.01, payment_duration + 30, fault)
        self._record(trace_id, f"{trace_id}-payment", f"{trace_id}-cart", "payment", start_time + 0.02, payment_duration, fault)

    def test_live_fault_is_consumed_and_rollbacks_are_persisted(self):
        arm_response = self.client.post("/api/fault", json={"service": "payment", "delay_ms": 1500, "request_count": 2})
        self.assertEqual(arm_response.status_code, 201)
        self.assertEqual(arm_response.get_json()["fault"]["service"], "payment")
        self.assertEqual(arm_response.get_json()["fault"]["remaining_requests"], 2)
        self.assertEqual(tracer.consume_fault("payment")["remaining_requests"], 1)
        self.assertEqual(tracer.consume_fault("payment")["delay_ms"], 1500)
        self.assertIsNone(tracer.consume_fault("payment"))

        for index in range(5):
            self._seed_trace(f"normal-{index}", 1000 + index, 30)
        self._seed_trace("live-fault", 2000, 1530, fault=True)

        trace_response = self.client.get("/api/trace/live-fault")
        self.assertEqual(trace_response.status_code, 200)
        trace = trace_response.get_json()
        self.assertEqual(trace["root_cause_service"], "payment")
        self.assertEqual({row["service"] for row in trace["rollbacks"]}, {"auth", "cart"})
        self.assertTrue(all(row["status"] == "pending" for row in trace["rollbacks"]))

        time.sleep(1.1)
        rollback_response = self.client.get("/api/trace/live-fault/rollbacks")
        rows = rollback_response.get_json()["rollbacks"]
        self.assertTrue(all(row["status"] == "completed" for row in rows))

        state_response = self.client.get("/api/trace/live-fault/state-history")
        state_events = state_response.get_json()["state_history"]
        self.assertIn("fault_detected", {event["event_type"] for event in state_events})
        completed_events = [event for event in state_events if event["event_type"] == "rollback_completed"]
        self.assertEqual({event["service"] for event in completed_events}, {"auth", "cart"})
        self.assertTrue(all(
            event["current_state"]["operation_count"] == event["previous_state"]["operation_count"] - 1
            for event in completed_events
        ))

        anomaly_response = self.client.get("/api/anomalies")
        points = [point for point in anomaly_response.get_json()["anomalies"] if point["trace_id"] == "live-fault"]
        self.assertEqual({point["service"] for point in points}, {"auth", "cart", "payment"})
        self.assertTrue(next(point for point in points if point["service"] == "payment")["is_anomalous"])

    def test_z_score_and_backward_traversal_still_detect_an_organic_anomaly(self):
        for index in range(20):
            self._seed_trace(f"baseline-{index}", 1000 + index, 30)
        self._seed_trace("organic-anomaly", 3000, 530)

        trace_response = self.client.get("/api/trace/organic-anomaly")
        trace = trace_response.get_json()
        self.assertEqual(trace["root_cause_service"], "payment")
        payment_node = next(node for node in trace["nodes"] if node["service"] == "payment")
        self.assertGreater(payment_node["z_score"], 2.0)
        self.assertEqual({row["service"] for row in trace["rollbacks"]}, {"auth", "cart"})


if __name__ == "__main__":
    unittest.main()
