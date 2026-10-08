"""Assignment 2 checks. Offline: no API key needed."""

import asyncio
import unittest

from multi_agent.coordinator import coverage_check, decompose, render_report, resolve, run_ticket
from multi_agent.fixtures import CONCERNS
from multi_agent.hooks import build_hooks
from multi_agent.ledger import Ledger
from multi_agent.results import Finding
from multi_agent.runtime import Worker
from multi_agent.specialists import policy_analyst
from multi_agent.specs import SPECIALIST_SPECS, unscoped

REFUND_899 = {"customer_id": "CUST-002", "order_id": "ORD-456", "amount_usd": 899.00, "reason": "crushed"}
REFUND_149 = {"customer_id": "CUST-001", "order_id": "ORD-123", "amount_usd": 149.99, "reason": "cracked"}


def run(coro):
    return asyncio.run(coro)


class ToolScopingTests(unittest.TestCase):
    def test_every_specialist_has_an_explicit_tools_list(self):
        for name, spec in SPECIALIST_SPECS.items():
            self.assertIsNotNone(spec.tools, name)

    def test_explicit_list_blocks_tools_outside_it(self):
        led = Ledger()
        w = Worker("policy-analyst", SPECIALIST_SPECS["policy-analyst"].tools, led, build_hooks(led))
        r = run(w.call("process_refund", {"order_id": "ORD-321", "customer_id": "CUST-002",
                                           "amount_usd": 10.0, "reason": "x"}))
        self.assertEqual(r["errorCategory"], "permission")
        self.assertEqual(led.refunds, [])

    def test_omitted_tools_field_gives_every_tool(self):
        led = Ledger()
        w = Worker("policy-analyst", unscoped("policy-analyst").tools, led, build_hooks(led))
        r = run(w.call("process_refund", {"order_id": "ORD-321", "customer_id": "CUST-002",
                                           "amount_usd": 10.0, "reason": "x"}))
        self.assertTrue(r["ok"])


class HookTests(unittest.TestCase):
    def test_one_hook_registered_against_the_refund_tool(self):
        hooks = build_hooks(Ledger())
        self.assertEqual(len(hooks["PreToolUse"]), 1)
        self.assertEqual(hooks["PreToolUse"][0].matcher, "process_refund")

    def test_over_ceiling_refund_denied_log_empty_and_escalated(self):
        led = Ledger()
        outcome = run(resolve("C1", "refund-processor", REFUND_899, led, build_hooks(led)))
        self.assertEqual(led.refunds, [])
        self.assertEqual(len(led.denials), 1)
        self.assertEqual(outcome.status, "escalated")
        self.assertEqual(len(led.tickets), 1)

    def test_under_ceiling_refund_allowed(self):
        led = Ledger()
        outcome = run(resolve("C1", "refund-processor", REFUND_149, led, build_hooks(led)))
        self.assertEqual(len(led.refunds), 1)
        self.assertEqual(outcome.status, "resolved")

    def test_ceiling_is_cumulative_per_order(self):
        led = Ledger()
        w = Worker("refund-processor", SPECIALIST_SPECS["refund-processor"].tools, led, build_hooks(led))
        args = {"order_id": "ORD-456", "customer_id": "CUST-002", "amount_usd": 300.0, "reason": "crushed"}
        self.assertTrue(run(w.call("process_refund", args))["ok"])
        second = run(w.call("process_refund", args))
        self.assertEqual(second["errorCategory"], "business")
        self.assertEqual(len(led.refunds), 1)


class ContextPassingTests(unittest.TestCase):
    def test_missing_status_gives_hedged_answer(self):
        led = Ledger()
        w = Worker("policy-analyst", SPECIALIST_SPECS["policy-analyst"].tools, led, build_hooks(led))
        out = run(policy_analyst("C3", {"order_id": "ORD-789"}, w))
        self.assertIn("cannot confirm", out.findings[0].claim)

    def test_stated_status_gives_definitive_answer(self):
        led = Ledger()
        w = Worker("policy-analyst", SPECIALIST_SPECS["policy-analyst"].tools, led, build_hooks(led))
        out = run(policy_analyst("C3", {"order_id": "ORD-789", "order_status": "in_transit"}, w))
        self.assertIn("No return window has started", out.findings[0].claim)


class ContractAndFailureTests(unittest.TestCase):
    def test_finding_without_source_is_rejected(self):
        with self.assertRaises(ValueError):
            Finding("C1", "claim", "evidence", "")

    def test_access_failure_and_valid_empty_are_distinct(self):
        led = Ledger()
        w = Worker("order-investigator", SPECIALIST_SPECS["order-investigator"].tools, led, build_hooks(led))
        denied = run(w.call("find_charges", {"order_id": "ORD-456", "customer_id": "CUST-001"}))
        empty = run(w.call("find_charges", {"order_id": "ORD-321", "customer_id": "CUST-002"}))
        self.assertEqual(denied["errorCategory"], "permission")
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["charges"], [])

    def test_coverage_before_finds_gaps_after_closes_them(self):
        self.assertEqual(coverage_check(CONCERNS, decompose("before")), ["C2", "C4"])
        self.assertEqual(coverage_check(CONCERNS, decompose("after")), [])

    def test_ticket_run_names_unresolved_concern_and_keeps_attribution(self):
        led = Ledger(charge_failures_left=2)
        outcomes, _ = run(run_ticket(led, []))
        c2 = outcomes["C2"]
        self.assertEqual(c2.status, "unresolved")
        self.assertEqual(c2.failure.errorCategory, "transient")
        self.assertTrue(c2.failure.isRetryable)
        self.assertTrue(c2.failure.attempted and c2.failure.partial and c2.failure.alternatives)
        self.assertEqual(outcomes["C4"].status, "escalated")
        report = render_report(outcomes)
        self.assertIn("Unresolved: C2", report)
        for o in outcomes.values():
            for f in o.findings:
                self.assertIn(f.source, report)


if __name__ == "__main__":
    unittest.main()
