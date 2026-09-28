import ast
import json
import random
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


class MemberCandidate:
    def __init__(self, name: str, user_id: str, avatar: str):
        self.name = name
        self.user_id = user_id
        self.avatar = avatar


def _load_module_functions(claim_root: Path | None = None) -> dict:
    # 认领表相关的函数也要一并提取：_pick_group_member 现在依赖它们做按天按群去重。
    wanted = {
        '_pick_group_member', '_loli_enabled',
        '_member_claim_path', '_member_claim_scope',
        '_load_member_claims', '_write_member_claims',
    }
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ]
    for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        body.extend(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
        )
    root = claim_root or Path(tempfile.mkdtemp())
    namespace = {
        'Event': object,
        'MemberCandidate': MemberCandidate,
        'random': random,
        'asyncio': asyncio,
        'json': json,
        'Path': Path,
        'logger': SimpleNamespace(debug=lambda *a: None, warning=lambda *a: None),
        'LOG_PREFIX': '[TEST]',
        '_cfg_bool': lambda key, default=False: default,
        # 认领表落在临时目录里，测试之间互不影响，也不碰真实数据目录
        '_member_claim_lock': asyncio.Lock(),
        '_custom_upload_data_root': lambda: root,
        '_today_key': lambda: '2026-01-01',
    }
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, 'test_fns', 'exec'), namespace)
    return namespace


class MarryMemberSelfExclusionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ns = _load_module_functions(Path(self._tmp.name))
        self.pick_fn = self.ns['_pick_group_member']

    async def test_pick_group_member_excludes_caller_and_bot(self) -> None:
        ev = SimpleNamespace(
            user_id="123456",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Self", user_id="123456", avatar="avatar_self"),
            MemberCandidate(name="Bot", user_id="999999", avatar="avatar_bot"),
            MemberCandidate(name="Friend", user_id="654321", avatar="avatar_friend"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42))
        self.assertIsNotNone(picked)
        self.assertEqual(picked.user_id, "654321")
        self.assertEqual(picked.name, "Friend")

    async def test_pick_group_member_returns_none_when_only_caller_and_bot_present(self) -> None:
        ev = SimpleNamespace(
            user_id="123456",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Self", user_id="123456", avatar="avatar_self"),
            MemberCandidate(name="Bot", user_id="999999", avatar="avatar_bot"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42))
        self.assertIsNone(picked)

    async def test_two_callers_in_one_group_cannot_marry_the_same_member(self) -> None:
        """同一天同一个群，一个群友只能被一个人娶到。"""
        candidates = (
            MemberCandidate(name="A", user_id="111", avatar="a"),
            MemberCandidate(name="B", user_id="222", avatar="b"),
        )
        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        first = await self.pick_fn(
            SimpleNamespace(user_id="900", bot_self_id="999", group_id="888", bot_id="onebot"),
            random.Random(1),
        )
        second = await self.pick_fn(
            SimpleNamespace(user_id="901", bot_self_id="999", group_id="888", bot_id="onebot"),
            random.Random(1),
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        # 同一个 rng 种子，没有认领表的话两人必定抽到同一个
        self.assertNotEqual(first.user_id, second.user_id)

    async def test_third_caller_gets_none_when_pool_is_claimed_out(self) -> None:
        """候选被认领光之后，后来者拿不到人，而不是复用别人的老婆。"""
        candidates = (
            MemberCandidate(name="A", user_id="111", avatar="a"),
            MemberCandidate(name="B", user_id="222", avatar="b"),
        )
        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        for uid in ("900", "901"):
            await self.pick_fn(
                SimpleNamespace(user_id=uid, bot_self_id="999", group_id="888", bot_id="onebot"),
                random.Random(1),
            )
        third = await self.pick_fn(
            SimpleNamespace(user_id="902", bot_self_id="999", group_id="888", bot_id="onebot"),
            random.Random(1),
        )
        self.assertIsNone(third)

    async def test_same_caller_gets_a_stable_result_within_the_day(self) -> None:
        """本人当天重复触发，结果必须稳定。"""
        candidates = tuple(
            MemberCandidate(name=f"M{i}", user_id=str(i), avatar=f"a{i}") for i in range(10)
        )
        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        ev = SimpleNamespace(user_id="900", bot_self_id="999", group_id="888", bot_id="onebot")
        first = await self.pick_fn(ev, random.Random(1))
        again = await self.pick_fn(ev, random.Random(7))   # 换种子也要拿到同一个
        self.assertIsNotNone(first)
        self.assertEqual(first.user_id, again.user_id)

    async def test_claims_are_scoped_per_group(self) -> None:
        """认领表按群分桶：别的群娶过的人，本群仍然可娶。"""
        candidates = (MemberCandidate(name="Only", user_id="111", avatar="a"),)
        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        a = await self.pick_fn(
            SimpleNamespace(user_id="900", bot_self_id="999", group_id="888", bot_id="onebot"),
            random.Random(1),
        )
        b = await self.pick_fn(
            SimpleNamespace(user_id="901", bot_self_id="999", group_id="777", bot_id="onebot"),
            random.Random(1),
        )
        self.assertEqual(a.user_id, "111")
        self.assertEqual(b.user_id, "111")

    async def test_pick_group_member_respects_custom_exclude_user_id(self) -> None:
        ev = SimpleNamespace(
            user_id="caller",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Target", user_id="777777", avatar="avatar_target"),
            MemberCandidate(name="Friend", user_id="654321", avatar="avatar_friend"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42), exclude_user_id="777777")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.user_id, "654321")


class DailyLoliConfigTests(unittest.TestCase):
    def test_loli_enabled_defaults_to_true(self) -> None:
        ns = _load_module_functions()
        self.assertTrue(ns['_loli_enabled']())


if __name__ == "__main__":
    unittest.main()
