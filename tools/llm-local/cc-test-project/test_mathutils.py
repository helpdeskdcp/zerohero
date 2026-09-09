import unittest
from mathutils import add, is_prime


class TestMathUtils(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_is_prime(self):
        self.assertTrue(is_prime(7))
        self.assertFalse(is_prime(8))


if __name__ == "__main__":
    unittest.main()
