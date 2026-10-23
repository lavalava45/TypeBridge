import unittest
from unittest.mock import patch

from single_instance import exclusive_live_hook


class SingleInstanceTests(unittest.TestCase):
    def test_second_live_hook_is_rejected(self):
        # Do not collide with the actual live TypeBridge's named mutex.
        with patch("single_instance.MUTEX_NAME", "Local\\TypeBridge-UnitTestOnly-v1"):
            with exclusive_live_hook():
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with exclusive_live_hook():
                        self.fail("second hook must not acquire the mutex")
            with exclusive_live_hook():
                pass


if __name__ == "__main__":
    unittest.main()
