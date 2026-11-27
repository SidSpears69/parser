"""Offline tests for the supported SmartCaptcha image flow."""
import base64
import unittest
from unittest.mock import Mock, patch

from web_parser.captcha_service import CaptchaServiceCancelled, CaptchaServiceError
# Test the image host and redirect guards directly.
# noinspection PyProtectedMember
from web_parser.yandex_captcha import _read_image, _trusted_url, solve_yandex_captcha


def data(value):
    return "data:image/png;base64," + base64.b64encode(value).decode("ascii")


def picture(width, height, payload):
    image = Mock()
    image.is_visible.return_value = True
    image.evaluate.return_value = {"src": data(payload), "width": width, "height": height}
    image.screenshot.return_value = payload
    return image


class YandexCaptchaTests(unittest.TestCase):
    def setUp(self):
        self.page = Mock()
        self.page.evaluate.side_effect = [False, True]
        self.frame = Mock()
        self.frame.url = "https://smartcaptcha.yandexcloud.net/challenge"
        self.page.frames = [self.frame]
        self.main = picture(320, 180, b"main-original")
        self.instruction = picture(480, 180, b"instruction-original")
        self.main.bounding_box.return_value = {"x": 17, "y": 20, "width": 160, "height": 90}
        self.frame.locator.return_value.all.return_value = [self.main, self.instruction]
        self.frame.get_by_role.return_value.count.return_value = 0

    def solve(self):
        return solve_yandex_captcha(self.page, api_key="key", cancelled=lambda: False)

    @patch("web_parser.yandex_captcha.solve_yandex_coordinates", return_value=[(100, 60), (50, 20)])
    def test_solves_in_frame_with_original_images_and_scaled_ordered_coordinates(self, solver):
        self.assertTrue(self.solve())
        args, kwargs = solver.call_args
        self.assertEqual(args, (b"main-original", b"instruction-original"))
        self.assertEqual(kwargs["api_key"], "key")
        self.assertEqual(self.main.click.call_args_list[0].kwargs["position"], {"x": 50, "y": 30})
        self.assertEqual(self.main.click.call_args_list[1].kwargs["position"], {"x": 25, "y": 10})
        self.page.request.get.assert_not_called()

    def test_out_of_bounds_coordinates_do_not_click(self):
        with patch("web_parser.yandex_captcha.solve_yandex_coordinates", return_value=[(320, 0)]):
            self.assertFalse(self.solve())
        self.main.click.assert_not_called()

    def test_refreshed_pixels_with_same_url_are_not_clicked(self):
        self.main.screenshot.side_effect = [b"old image", b"new image"]
        with patch("web_parser.yandex_captcha.solve_yandex_coordinates", return_value=[(30, 40)]):
            self.assertFalse(self.solve())
        self.main.click.assert_not_called()

    def test_changed_url_is_not_clicked(self):
        old = self.main.evaluate.return_value
        self.main.evaluate.side_effect = [old, {**old, "src": data(b"new")}]
        with patch("web_parser.yandex_captcha.solve_yandex_coordinates", return_value=[(30, 40)]):
            self.assertFalse(self.solve())
        self.main.click.assert_not_called()

    def test_solution_requires_real_page_result(self):
        self.page.evaluate.side_effect = None
        self.page.evaluate.return_value = False
        with patch("web_parser.yandex_captcha.solve_yandex_coordinates", return_value=[(30, 40)]):
            self.assertFalse(self.solve())
        self.assertEqual(self.page.wait_for_timeout.call_count, 40)

    def test_service_errors_propagate(self):
        with (
            patch("web_parser.yandex_captcha.solve_yandex_coordinates", side_effect=CaptchaServiceError("error")),
            self.assertRaises(CaptchaServiceError),
        ):
            self.solve()

    def test_cancel_is_preserved(self):
        with self.assertRaises(CaptchaServiceCancelled):
            solve_yandex_captcha(self.page, api_key="key", cancelled=lambda: True)

    @patch("web_parser.yandex_captcha.solve_yandex_coordinates")
    def test_unknown_image_sizes_do_not_send_challenge(self, solver):
        self.instruction.evaluate.return_value["width"] = 479
        self.page.evaluate.side_effect = None
        self.page.evaluate.return_value = False
        self.frame.locator.return_value.all.return_value = [self.main, self.instruction]
        self.assertFalse(self.solve())
        solver.assert_not_called()

    def test_external_image_host_is_not_requested(self):
        self.assertFalse(_trusted_url("https://yandex.ru.evil.example/image"))
        self.assertIsNone(_read_image(self.page, "https://evil.example/image"))
        self.page.request.get.assert_not_called()

    def test_download_does_not_follow_redirects(self):
        response = self.page.request.get.return_value
        response.ok = True
        response.headers = {"content-type": "image/png"}
        response.body.return_value = b"original"
        self.assertEqual(_read_image(self.page, "https://captcha.yandex.ru/img"), b"original")
        self.page.request.get.assert_called_once_with("https://captcha.yandex.ru/img", timeout=5000, max_redirects=0)


if __name__ == "__main__":
    unittest.main()
