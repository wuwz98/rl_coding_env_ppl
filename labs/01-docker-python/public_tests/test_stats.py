import unittest

from stats import largest


class LargestTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(largest([2, 7, 3]), 7)

    def test_mixed(self):
        self.assertEqual(largest([-4, 2, -1]), 2)

    def test_empty(self):
        with self.assertRaises(ValueError):
            largest([])
