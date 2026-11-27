"""Offline tests of 2Captcha request, polling and cancellation."""

import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from web_parser.captcha_service import (
    CaptchaServiceCancelled, CaptchaServiceError, solve_yandex_coordinates,
)


class CaptchaServiceTests(unittest.TestCase):
    @staticmethod
    def solve(cancelled=lambda: False):
        return solve_yandex_coordinates(b"main", b"hint", api_key="secret-key", cancelled=cancelled)

    @patch("web_parser.captcha_service._wait")
    @patch("web_parser.captcha_service.build_opener")
    def test_submits_images_and_polls_in_order(self, opener, _wait):
        responses = [
            {"errorId": 0, "taskId": 123},
            {"errorId": 0, "status": "processing"},
            {"errorId": 0, "status": "ready", "solution": {
                "coordinates": [{"x": 4, "y": 5}, {"x": 1, "y": 2}],
            }},
        ]
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.side_effect = [json.dumps(item).encode() for item in responses]
        self.assertEqual(self.solve(), [(4, 5), (1, 2)])
        calls = opener.return_value.open.call_args_list
        create_request = calls[0].args[0]
        self.assertEqual(create_request.full_url, "https://api.2captcha.com/createTask")
        self.assertEqual(json.loads(create_request.data), {
            "clientKey": "secret-key", "task": {
                "type": "SmartCaptchaTask", "image": "bWFpbg==", "imgInstructions": "aGludA==",
                "comment": "select objects in the order of the instruction",
            },
        })
        poll_request = calls[1].args[0]
        self.assertEqual(poll_request.full_url, "https://api.2captcha.com/getTaskResult")
        self.assertEqual(json.loads(poll_request.data), {"clientKey": "secret-key", "taskId": 123})
        self.assertEqual(len(calls), 3)

    @patch("web_parser.captcha_service.build_opener")
    def test_network_error_does_not_disclose_secret(self, opener):
        opener.return_value.open.side_effect = URLError("secret-key")
        with self.assertRaises(CaptchaServiceError) as caught:
            self.solve()
        self.assertNotIn("secret-key", str(caught.exception))

    @patch("web_parser.captcha_service.build_opener")
    def test_api_error_is_reported_without_untrusted_description(self, opener):
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b'{"errorId": 10, "errorDescription": "secret-key"}'
        with self.assertRaisesRegex(CaptchaServiceError, "10") as caught:
            self.solve()
        self.assertNotIn("secret-key", str(caught.exception))

    @patch("web_parser.captcha_service._wait")
    @patch("web_parser.captcha_service._post")
    def test_rejects_invalid_coordinates(self, post, _wait):
        post.side_effect = [
            {"taskId": 123},
            {"status": "ready", "solution": {"coordinates": [{"x": True, "y": -1}]}},
        ]
        with self.assertRaisesRegex(CaptchaServiceError, "координаты"):
            self.solve()

    @patch("web_parser.captcha_service._post")
    def test_cancel_before_submission(self, post):
        with self.assertRaises(CaptchaServiceCancelled):
            self.solve(lambda: True)
        post.assert_not_called()

    @patch("web_parser.captcha_service._post")
    def test_cancel_during_wait(self, post):
        post.return_value = {"taskId": 123}
        cancelled = Mock(side_effect=[False, False, True])
        with self.assertRaises(CaptchaServiceCancelled):
            self.solve(cancelled)
        post.assert_called_once()

    @patch("web_parser.captcha_service.time.monotonic", side_effect=[0, 121])
    @patch("web_parser.captcha_service._post", return_value={"taskId": 123})
    def test_timeout_does_not_poll(self, post, _clock):
        with self.assertRaisesRegex(CaptchaServiceError, "Истекло время"):
            self.solve()
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
