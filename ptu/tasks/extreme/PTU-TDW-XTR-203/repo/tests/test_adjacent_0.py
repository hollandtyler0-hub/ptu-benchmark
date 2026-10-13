import unittest
from billing_core.plans.quantities import amount
class Adjacent(unittest.TestCase):
    def test_0(self):
        self.assertEqual(amount({'unit_amount':100,'quantity':3}),300)
