import json
import unittest
import test_live_observer as fixtures
record = fixtures.record


class PlanTests(unittest.TestCase):
    setUp = fixtures.LiveObserverTests.setUp
    tearDown = fixtures.LiveObserverTests.tearDown
    def plan(self, call_id, items):
        self.observer.consume(record('response_item', {'type': 'function_call', 'name': 'functions.update_plan',
            'call_id': call_id, 'arguments': json.dumps({'plan': items})}, 5))

    def finish(self, call_id, error=False):
        self.observer.consume(record('response_item', {'type': 'function_call_output', 'call_id': call_id,
            'output': {'isError': error, 'text': 'recorded'}}, 6))

    def test_request_alone_is_not_an_accepted_plan(self):
        self.plan('p1', [{'step': '审查材料', 'status': 'in_progress'}])
        self.assertIsNone(self.observer.snapshot()['turns'][-1]['plan'])

    def test_accepted_plan_has_stable_ids_across_reordering_and_status(self):
        self.plan('p1', [{'step': '审查材料', 'status': 'in_progress'}, {'step': '验证', 'status': 'pending'}])
        self.finish('p1')
        before = {x['title']: x['id'] for x in self.observer.snapshot()['turns'][-1]['plan']['items']}
        self.plan('p2', [{'step': '验证', 'status': 'in_progress'}, {'step': '审查材料', 'status': 'completed'}])
        self.finish('p2')
        after = {x['title']: x['id'] for x in self.observer.snapshot()['turns'][-1]['plan']['items']}
        self.assertEqual(before, after)

    def test_failed_update_keeps_previous_plan(self):
        self.plan('p1', [{'step': '旧计划', 'status': 'pending'}]); self.finish('p1')
        self.plan('p2', [{'step': '失败计划', 'status': 'completed'}]); self.finish('p2', True)
        self.assertEqual(self.observer.snapshot()['turns'][-1]['plan']['items'][0]['title'], '旧计划')

    def test_plans_are_bounded_and_redacted(self):
        self.plan('p1', [{'step': 'password="fixture-secret" ' + 'x' * 1000, 'status': 'pending'}] * 100)
        self.finish('p1')
        plan = self.observer.snapshot()['turns'][-1]['plan']
        self.assertEqual(len(plan['items']), 80)
        self.assertNotIn('fixture-secret', json.dumps(plan))
        self.assertEqual(len({x['id'] for x in plan['items']}), 80)

    def test_new_turn_does_not_inherit_plan(self):
        self.plan('p1', [{'step': '旧计划', 'status': 'completed'}]); self.finish('p1')
        self.observer.consume(record('turn_context', {'turn_id': 'turn-b'}, 10))
        self.assertIsNone(self.observer.snapshot()['turns'][-1]['plan'])


if __name__ == '__main__':
    unittest.main()
