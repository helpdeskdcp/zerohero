import unittest
from textstats import char_count, word_frequency


class TestTextStats(unittest.TestCase):
    def test_char_count(self):
        self.assertEqual(char_count("a b c"), 3)

    def test_word_frequency_basic(self):
        self.assertEqual(word_frequency("the cat the dog"),
                         {"the": 2, "cat": 1, "dog": 1})

    def test_word_frequency_case_and_punct(self):
        self.assertEqual(word_frequency("Hello, hello! World."),
                         {"hello": 2, "world": 1})


if __name__ == "__main__":
    unittest.main()
