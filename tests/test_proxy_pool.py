"""Tests for the shared per-marketplace proxy preferences."""

import unittest

from web_parser import config
from web_parser.proxy_pool import MARKETPLACES, ProxyPool


class ProxyPoolTests(unittest.TestCase):
    def setUp(self):
        self.pool = ProxyPool()
        self.slots = list(config.default_proxy_pool())

    def test_empty_pool_uses_direct_connection(self):
        routes = self.pool.routes_for_walk(self.slots)
        self.assertEqual(routes.proxies, {0: None})
        self.assertEqual(routes.candidates, dict.fromkeys(MARKETPLACES, (0,)))

    def test_routes_start_in_slot_order_and_remember_success_independently(self):
        self.slots[0]["server"] = "socks5://one.example:1080"
        self.slots[2] = {
            "server": "http://three.example:8080", "username": "user", "password": "secret",
        }
        routes = self.pool.routes_for_walk(self.slots)
        self.assertEqual(routes.candidates["Ozon"], (1, 3, 0))
        self.assertEqual(routes.proxies[3], self.slots[2])
        routes.remember("Ozon", 3)

        next_routes = self.pool.routes_for_walk(self.slots)
        self.assertEqual(next_routes.candidates["Ozon"], (3, 1, 0))
        self.assertEqual(next_routes.candidates["Wildberries"], (1, 3, 0))

    def test_settings_change_discards_preferences_and_ignores_old_walk_results(self):
        self.slots[0]["server"] = "http://one.example:8080"
        self.slots[1]["server"] = "http://two.example:8080"
        routes = self.pool.routes_for_walk(self.slots)
        routes.remember("Ozon", 2)
        self.slots[1]["server"] = "http://updated.example:8080"
        self.assertEqual(self.pool.routes_for_walk(self.slots).candidates["Ozon"], (1, 2, 0))

        routes.remember("Ozon", 2)
        self.assertEqual(self.pool.routes_for_walk(self.slots).candidates["Ozon"], (1, 2, 0))

    def test_partial_slot_is_rejected_without_discarding_working_route(self):
        self.slots[0]["server"] = "http://one.example:8080"
        self.slots[1]["server"] = "http://two.example:8080"
        self.pool.routes_for_walk(self.slots).remember("Ozon", 2)
        invalid = [slot.copy() for slot in self.slots]
        invalid[2]["username"] = "orphan-user"
        with self.assertRaisesRegex(config.ConfigError, "Прокси №3"):
            self.pool.routes_for_walk(invalid)
        self.assertEqual(self.pool.routes_for_walk(self.slots).candidates["Ozon"], (2, 1, 0))


if __name__ == "__main__":
    unittest.main()
