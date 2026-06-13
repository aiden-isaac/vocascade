import unittest
from vocascade.tts.sentence_splitter import split_sentences, SentenceChunk

class TestClauseSentenceSplitter(unittest.TestCase):
    def test_clause_split_under_threshold(self):
        # Accumulated segment is 6 words (< 8), should not split
        text = "Hello my dear friend, how are you?"
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], SentenceChunk("Hello my dear friend, how are you?", False))

    def test_clause_split_comma(self):
        # "This is a very long sentence with many words before the comma" (12 words)
        # Should split on the comma and replace it with a period
        text = "This is a very long sentence with many words before the comma, and then we have some more words here."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], SentenceChunk("This is a very long sentence with many words before the comma.", False))
        self.assertEqual(chunks[1], SentenceChunk("and then we have some more words here.", False))

    def test_clause_split_semicolon(self):
        # "This is a really long sentence that will be split by a semicolon" (13 words)
        text = "This is a really long sentence that will be split by a semicolon; hopefully it works nicely."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], SentenceChunk("This is a really long sentence that will be split by a semicolon.", False))
        self.assertEqual(chunks[1], SentenceChunk("hopefully it works nicely.", False))

    def test_clause_split_colon(self):
        # "Here is a rather lengthy description that explains the next part" (11 words)
        text = "Here is a rather lengthy description that explains the next part: it is simple."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], SentenceChunk("Here is a rather lengthy description that explains the next part.", False))
        self.assertEqual(chunks[1], SentenceChunk("it is simple.", False))

    def test_clause_split_em_dash(self):
        # "He had a very clear goal in his mind throughout the journey — to succeed." (14 words)
        text = "He had a very clear goal in his mind throughout the journey — to succeed."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], SentenceChunk("He had a very clear goal in his mind throughout the journey.", False))
        self.assertEqual(chunks[1], SentenceChunk("to succeed.", False))

    def test_clause_split_multiple(self):
        text = "This is a very long sentence with many words before the comma, and then we have some more words here, which will also be split."
        # segment 1: "This is a very long sentence with many words before the comma," (12 words) -> split
        # segment 2: "and then we have some more words here," (8 words) -> accumulated is 8 words <= 8, no split!
        # segment 3: "which will also be split."
        # Total words in remaining segment: "and then we have some more words here, which will also be split."
        # "and then we have some more words here" is 8 words. 
        # Followed by comma. Since word count is 8 (not > 8), it won't split on the second comma.
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], SentenceChunk("This is a very long sentence with many words before the comma.", False))
        self.assertEqual(chunks[1], SentenceChunk("and then we have some more words here, which will also be split.", False))

if __name__ == "__main__":
    unittest.main()
