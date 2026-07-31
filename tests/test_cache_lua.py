"""
Regression tests that actually EXECUTE the lua in core/cache/scripts/.

Every failure encoded here was a real defect found on 2026-07-31, and every one of
them was invisible to the mock-based tests in test_cache_service.py. If you change a
.lua file, this is the file that has to stay green.

Requires fakeredis[lua] (see requirements-dev.txt); skipped when it is absent so a
production install without dev dependencies does not fail.
"""
import json
import unittest

from django.conf import settings

from core.cache import get_scope_index_key, resolve_idx_keys
from core.cache.service import CachingService
from tests.utils import make_test_redis


class CacheLuaTests(unittest.TestCase):

    TTL = 300

    def setUp(self):
        # Real redis when AUTOFLOW_TEST_REDIS_URL is set, fakeredis otherwise.
        self.redis = make_test_redis(self)
        self.service = CachingService(self.redis)
        self.service.load_scripts()

    # -- load_scripts ----------------------------------------------------------

    def test_every_declared_script_exists_on_disk(self):
        # AVAILABLE_SCRIPTS once listed 'bulk_invalidate' with no file behind it, so
        # load_scripts() raised FileNotFoundError the moment it was called.
        self.assertTrue(self.service.scripts)
        for name in self.service.scripts:
            self.assertTrue((settings.SCRIPTS_DIR / f"{name}.lua").exists(), name)

    # -- single_set / single_get ----------------------------------------------

    def test_set_then_get_round_trip(self):
        payload = json.dumps({"id": "u1", "name": "alice"})
        self.service.set("users/u1", payload, self.TTL)
        self.assertEqual(self.service.get("users/u1"), payload)

    def test_single_set_does_not_touch_any_index(self):
        # A detail fetch must never fabricate a one-member "complete" list.
        self.service.set("users/u1", '{"id":"u1"}', self.TTL)
        self.assertEqual(self.redis.keys("*"), ["users/u1"])

    # -- bulk_set / bulk_get ---------------------------------------------------

    def test_bulk_set_populates_the_index(self):
        # single_set.lua used to run `SADD data_key data_key`, which both left the
        # index empty and raised WRONGTYPE against the string SETEX had just written.
        self.service.bulk_set(
            "users/all",
            [("users/u1", '{"id":"u1"}'), ("users/u2", '{"id":"u2"}')],
            self.TTL,
        )
        self.assertEqual(
            self.redis.smembers("users/all"), {"users/u1", "users/u2"}
        )

    def test_bulk_get_returns_every_member(self):
        self.service.bulk_set(
            "users/all",
            [("users/u1", '{"id":"u1"}'), ("users/u2", '{"id":"u2"}')],
            self.TTL,
        )
        got = self.service.bulk_get("users/all")
        self.assertEqual(sorted(got), ['{"id":"u1"}', '{"id":"u2"}'])

    def test_cold_index_returns_none_not_empty_list(self):
        # THE important one. bulk_get used to return [] on a miss, and
        # get_cached_queryset only fell back to the database on None - so a cold
        # cache served an empty list to the client as though it were real data.
        self.assertIsNone(self.service.bulk_get("users/all"))

    def test_bulk_set_replaces_rather_than_merges(self):
        self.service.bulk_set("users/all", [("users/u1", '{"id":"u1"}')], self.TTL)
        self.service.bulk_set("users/all", [("users/u2", '{"id":"u2"}')], self.TTL)
        self.assertEqual(self.redis.smembers("users/all"), {"users/u2"})

    def test_index_carries_a_ttl(self):
        self.service.bulk_set("users/all", [("users/u1", '{"id":"u1"}')], self.TTL)
        self.assertGreater(self.redis.ttl("users/all"), 0)

    def test_partially_expired_index_is_a_miss_and_is_dropped(self):
        self.service.bulk_set(
            "users/all",
            [("users/u1", '{"id":"u1"}'), ("users/u2", '{"id":"u2"}')],
            self.TTL,
        )
        self.redis.delete("users/u1")  # simulate one member's ttl running out

        self.assertIsNone(self.service.bulk_get("users/all"))
        # and the stale index is cleaned up rather than left to rot
        self.assertEqual(self.redis.exists("users/all"), 0)

    # -- single_invalidate -----------------------------------------------------

    def test_invalidate_drops_the_object_and_the_lists(self):
        self.service.bulk_set(
            "users/all",
            [("users/u1", '{"id":"u1"}'), ("users/u2", '{"id":"u2"}')],
            self.TTL,
        )
        self.service.single_invalidate("users/u1", ["users/all"])

        self.assertIsNone(self.service.get("users/u1"))
        # The whole index goes, not just the one member: an index means "the complete
        # answer", and SREMing a member leaves a set that still claims completeness.
        self.assertEqual(self.redis.exists("users/all"), 0)

    # -- the design defects ----------------------------------------------------

    def test_a_narrow_role_cannot_poison_a_wider_index(self):
        """
        A branch manager listing their own branch must not publish that result as the
        complete answer for super_admin. Previously bulk_cache wrote every object into
        every index it qualified for, so super_admin got a silent 2-of-4 cache hit.
        """
        branch_a_users = [
            {"id": "u1", "tenant": "t1", "branch": "bA"},
            {"id": "u2", "tenant": "t1", "branch": "bA"},
        ]

        # branch manager's list request caches under its own scope only
        idx = get_scope_index_key("User", "branch", "t1", "bA")
        self.assertEqual(idx, "users/branch:bA")
        self.service.bulk_set(
            idx,
            [(f"users/{u['id']}", json.dumps(u)) for u in branch_a_users],
            self.TTL,
        )

        # super_admin's index is untouched -> falls through to the database
        self.assertIsNone(self.service.bulk_get("users/all"))

    def test_creating_a_row_invalidates_the_lists_it_would_appear_in(self):
        """
        New rows used to stay invisible until the TTL expired: nothing added them to a
        warm index, and the pre_save guard mistook the INSERT for an UPDATE.
        """
        all_users = [
            {"id": "u1", "tenant": "t1", "branch": "bA"},
            {"id": "u2", "tenant": "t1", "branch": "bB"},
        ]
        self.service.bulk_set(
            "users/all",
            [(f"users/{u['id']}", json.dumps(u)) for u in all_users],
            self.TTL,
        )
        self.assertIsNotNone(self.service.bulk_get("users/all"))

        # what core/signals.py does on post_save for a freshly created user
        new_user_idxs = resolve_idx_keys("User", "t1", "bA")
        self.assertIn("users/all", new_user_idxs)
        self.service.single_invalidate("users/u3", new_user_idxs)

        self.assertIsNone(self.service.bulk_get("users/all"))

    # -- registry consistency --------------------------------------------------

    def test_every_declared_index_starts_with_its_cache_key(self):
        # Branch declared 'branch/all' against cache_key 'branches/', and Template
        # declared 'templates:branch:' against 'templates/'. Both were permanent misses.
        from core.cache import REGISTRY

        for model, cfg in REGISTRY["MODEL_DATA"].items():
            if not cfg.get("CACHED"):
                continue
            prefix = cfg["cache_key"]
            for key in cfg["idx_keys"]:
                self.assertTrue(
                    key.startswith(prefix),
                    f"{model}: idx_key {key!r} does not start with cache_key {prefix!r}",
                )

    def test_unknown_scope_returns_no_index_instead_of_a_dead_key(self):
        # Tenant declares only 'tenants/all'; asking for a tenant-scoped index must
        # yield None so the caller goes to the database, not a key nobody writes.
        self.assertIsNone(get_scope_index_key("Tenant", "tenant", "t1", None))
        self.assertEqual(get_scope_index_key("Tenant", "all"), "tenants/all")

    def test_registry_lookups_tolerate_unknown_models(self):
        # core/signals.py is connected without a sender, so these run for every model
        # in the project. `.get(name).get('CACHED')` raised AttributeError on the
        # first CrontabSchedule save and made scheduled automations uncreatable.
        from core.cache import check_it_cacheable, get_cache_key, get_parent_info

        self.assertFalse(check_it_cacheable("CrontabSchedule"))
        self.assertIsNone(get_cache_key("Session"))
        self.assertIsNone(get_parent_info("ContentType"))
        self.assertEqual(resolve_idx_keys("Session"), [])
