"""Load a single module's *owned* unittest classes, not imported fixtures.

`unittest module` also discovers TestCase classes imported as helpers, causing
large accidental repetition during a full `discover` run.
"""
import importlib
import inspect
import sys
import unittest


def owned_suite(module):
    suite = unittest.TestSuite()
    for _, candidate in inspect.getmembers(module, inspect.isclass):
        if (issubclass(candidate, unittest.TestCase)
                and candidate.__module__ == module.__name__):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(candidate))
    return suite


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith('test_'):
        raise SystemExit('Usage: python test_matrix_module.py test_module_name')
    module = importlib.import_module(sys.argv[1])
    result = unittest.TextTestRunner(verbosity=2).run(owned_suite(module))
    return not result.wasSuccessful()


if __name__ == '__main__':
    sys.exit(main())
