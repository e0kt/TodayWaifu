import ast
import unittest
from typing import Any
from pathlib import Path
from collections import namedtuple

ROOT = Path(__file__).resolve().parents[1]

Role = namedtuple('Role', 'name')


class _Logger:
    def debug(self, *_args: Any) -> None:
        pass


def _load_untaken_roles():
    path = ROOT / 'TodayWaifu' / 'daily.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_untaken_roles'
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)

    def wife_state(raw: dict) -> str:
        if raw.get('divorced'):
            return 'divorced'
        if raw.get('stolen_by'):
            return 'lost_stolen'
        if raw.get('gifted_to'):
            return 'lost_gifted'
        return 'owned'

    namespace: dict[str, Any] = {
        '_wife_state': wife_state,
        '_normalize_role_name': lambda name: name.strip(),
        'logger': _Logger(),
        'LOG_PREFIX': '',
    }
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace['_untaken_roles']


class UntakenRolesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.untaken = _load_untaken_roles()
        self.candidates = (Role('蕾缪安'), Role('釉瑚'), Role('达妮娅'))

    def names(self, pool) -> list[str]:
        return [role.name for role in pool]

    def test_role_held_by_another_member_is_excluded(self) -> None:
        context = {'wives': {'2926618851': {'name': '蕾缪安'}}}
        pool = self.untaken(self.candidates, context, 'wives', '499309036')
        self.assertEqual(self.names(pool), ['釉瑚', '达妮娅'])

    def test_own_record_does_not_block_self(self) -> None:
        context = {'wives': {'499309036': {'name': '蕾缪安'}}}
        pool = self.untaken(self.candidates, context, 'wives', '499309036')
        self.assertEqual(len(pool), 3)

    def test_released_records_free_the_role(self) -> None:
        context = {'wives': {
            'a': {'name': '蕾缪安', 'stolen_by': 'x'},
            'b': {'name': '釉瑚', 'divorced': True},
            'c': {'name': '达妮娅', 'gifted_to': 'y'},
        }}
        pool = self.untaken(self.candidates, context, 'wives', 'me')
        self.assertEqual(len(pool), 3)

    def test_compensation_wives_also_occupy(self) -> None:
        context = {'wives': {}, 'safe_wives': {'a': {'name': '釉瑚'}}}
        pool = self.untaken(self.candidates, context, 'wives', 'me')
        self.assertEqual(self.names(pool), ['蕾缪安', '达妮娅'])

    def test_other_buckets_do_not_interfere(self) -> None:
        context = {'nte_wives': {}, 'wives': {'a': {'name': '蕾缪安'}}}
        pool = self.untaken(self.candidates, context, 'nte_wives', 'me')
        self.assertEqual(len(pool), 3)

    def test_falls_back_to_full_pool_when_all_taken(self) -> None:
        context = {'wives': {
            'a': {'name': '蕾缪安'}, 'b': {'name': '釉瑚'}, 'c': {'name': '达妮娅'},
        }}
        pool = self.untaken(self.candidates, context, 'wives', 'me')
        self.assertEqual(len(pool), 3)


if __name__ == '__main__':
    unittest.main()
