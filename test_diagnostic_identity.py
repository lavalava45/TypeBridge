from unittest.mock import patch, MagicMock
import unittest
import diagnostic_identity as identity


class DiagnosticIdentityTests(unittest.TestCase):
    def tearDown(self):
        identity.computer_id.cache_clear()

    def test_id_stable_different_and_does_not_expose_raw_guid(self):
        values = []
        for guid in ('private-machine-one', 'private-machine-one', 'private-machine-two'):
            identity.computer_id.cache_clear()
            with patch.object(identity.winreg, 'OpenKey', return_value=MagicMock()), patch.object(identity.winreg, 'QueryValueEx', return_value=(guid, 1)):
                result = identity.computer_id()
            self.assertRegex(result, r'^TB-[0-9A-F]{12}$')
            self.assertNotIn(guid, result)
            values.append(result)
        self.assertEqual(values[0], values[1])
        self.assertNotEqual(values[0], values[2])

    def test_fallback_does_not_expose_hostname(self):
        identity.computer_id.cache_clear()
        with patch.object(identity.winreg, 'OpenKey', side_effect=PermissionError), patch.object(identity.socket, 'gethostname', return_value='private-hostname'):
            self.assertNotIn('private-hostname', identity.computer_id())


if __name__ == '__main__':
    unittest.main()
