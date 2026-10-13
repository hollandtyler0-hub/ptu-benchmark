import unittest
from billing_core.plans.intervals import monthly
class Adjacent(unittest.TestCase):
    def test_1(self):
        self.assertEqual(monthly({'interval':'month'}),True)
