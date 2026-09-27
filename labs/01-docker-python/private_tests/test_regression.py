import unittest

from stats import largest


class RegressionTests(unittest.TestCase):
    def test_all_negative(self):
        self.assertEqual(largest([-8, -2, -5]), -2)

    def test_single_negative(self):
        self.assertEqual(largest([-3]), -3)
