"""The input method mapper must never register an unexpected Windows layout."""

import unittest
from unittest.mock import Mock, patch

from engine import LayoutMapper, configured_layout_hkls, select_loaded_layouts


EN = 0x04090409
RU = 0x04190419
HE_STANDARD = 0xF03D040D
HE_NATIVE64 = 0xFFFFFFFFF03D040D
HE_LEGACY = 0x040D040D


class WindowsLayoutTests(unittest.TestCase):
    def test_mapper_uses_only_verified_loaded_hkls_when_preferences_missing(self):
        loaded = [EN, RU, HE_NATIVE64]
        api = Mock()
        def enumerate_hkls(count, output):
            if count:
                for i, hkl in enumerate(loaded):
                    output[i] = hkl
            return len(loaded)
        api.GetKeyboardLayoutList.side_effect = enumerate_hkls
        def unicode_map(vk, sc, state, output, capacity, flags, hkl):
            output.value = {0x0409: 'a', 0x0419: 'ф', 0x040D: 'ש'}[hkl & 0xFFFF]
            return 1
        api.ToUnicodeEx.side_effect = unicode_map
        with patch('engine.ctypes.WinDLL', return_value=api), \
                patch('engine.configured_layout_entries', return_value={}):
            mapper = LayoutMapper()
        self.assertEqual(mapper.layouts, {'en':EN,'ru':RU,'he':HE_NATIVE64})
        api.LoadKeyboardLayoutW.assert_not_called()

    def test_mapper_refuses_unverifiable_fallback_without_loading(self):
        loaded = [EN, RU, HE_NATIVE64]
        api = Mock()
        def enumerate_hkls(count, output):
            if count:
                for i, hkl in enumerate(loaded):
                    output[i] = hkl
            return len(loaded)
        api.GetKeyboardLayoutList.side_effect = enumerate_hkls
        api.ToUnicodeEx.return_value = 0
        with patch('engine.ctypes.WinDLL', return_value=api), \
                patch('engine.configured_layout_entries', return_value={}):
            with self.assertRaisesRegex(OSError, 'cannot be verified'):
                LayoutMapper()
        api.LoadKeyboardLayoutW.assert_not_called()

    def test_user_configured_hebrew_standard_wins_even_if_legacy_is_first(self):
        layouts = select_loaded_layouts(
            [HE_LEGACY, RU, EN, HE_NATIVE64], {0x040D: HE_STANDARD})
        self.assertEqual(layouts, {"ru": RU, "en": EN, "he": HE_NATIVE64})

    def test_never_loads_missing_hebrew_as_side_effect(self):
        with self.assertRaisesRegex(OSError, "HE keyboard layout"):
            select_loaded_layouts([RU, EN], {0x040D: HE_STANDARD})

    def test_graceful_fallback_when_user_preferences_unavailable(self):
        layouts = select_loaded_layouts([RU, EN, HE_STANDARD], {})
        self.assertEqual(layouts["he"], HE_STANDARD)

    def test_mapper_only_enumerates_previously_loaded_hkls(self):
        loaded = [HE_LEGACY, EN, RU, HE_NATIVE64]
        api = Mock()

        def get_list(capacity, buffer):
            if capacity == 0:
                return len(loaded)
            for index, hkl in enumerate(loaded):
                buffer[index] = hkl
            return len(loaded)

        api.GetKeyboardLayoutList.side_effect = get_list
        klids = {0x0409: ("00000409", EN), 0x0419: ("00000419", RU),
                 0x040D: ("0002040d", HE_STANDARD)}
        api.LoadKeyboardLayoutW.side_effect = lambda klid, flags: {
            "00000409": EN, "00000419": RU, "0002040d": HE_NATIVE64}[klid]
        with (patch("engine.ctypes.WinDLL", return_value=api),
              patch("engine.configured_layout_entries", return_value=klids)):
            mapper = LayoutMapper()
        self.assertEqual(mapper.layouts["he"], HE_NATIVE64)
        self.assertEqual(api.GetKeyboardLayoutList.call_count, 2)
        self.assertEqual(api.LoadKeyboardLayoutW.call_count, 3)
        self.assertTrue(all(args.args[1] == 0x80
                            for args in api.LoadKeyboardLayoutW.call_args_list))
        self.assertNotIn("0000040d", [args.args[0] for args in
                                      api.LoadKeyboardLayoutW.call_args_list])

    def test_refuses_if_binding_disagrees_with_previously_loaded_hkl(self):
        api = Mock()
        loaded = [EN, RU, HE_STANDARD]

        def get_list(count, output):
            if count == 0:
                return len(loaded)
            for index, value in enumerate(loaded):
                output[index] = value
            return len(loaded)

        api.GetKeyboardLayoutList.side_effect = get_list
        api.LoadKeyboardLayoutW.return_value = HE_LEGACY
        with (patch("engine.ctypes.WinDLL", return_value=api),
              patch("engine.configured_layout_entries", return_value={
                  0x0409: ("00000409", EN), 0x0419: ("00000419", RU),
                  0x040D: ("0002040d", HE_STANDARD)})):
            with self.assertRaisesRegex(OSError, "did not bind"):
                LayoutMapper()

    def test_configured_layout_uses_preload_substitution_for_hebrew_standard(self):
        # Integration with read-only Windows keyboard configuration. This
        # assertion is independent of which layouts are *currently* loaded.
        self.assertIn(configured_layout_hkls().get(0x040D),
                      (None, HE_STANDARD, HE_LEGACY))


if __name__ == "__main__":
    unittest.main()
