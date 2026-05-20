import unittest
from voice_satellite.tts import split_sentences, SentenceChunk

class TestSentenceSplitter(unittest.TestCase):
    def test_basic_splitting(self):
        text = "Hello master. How are you today? Ordis is ready."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], SentenceChunk("Hello master.", False))
        self.assertEqual(chunks[1], SentenceChunk("How are you today?", False))
        self.assertEqual(chunks[2], SentenceChunk("Ordis is ready.", False))

    def test_glitch_tag_extraction(self):
        text = "Hello master. <glitch>— PURGE THEM ALL —</glitch> uh, Ordis is back."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], SentenceChunk("Hello master.", False))
        self.assertEqual(chunks[1], SentenceChunk("— PURGE THEM ALL —.", True))
        self.assertEqual(chunks[2], SentenceChunk("uh, Ordis is back.", False))

    def test_filtering_non_alphanumeric(self):
        text = "Hello. !!!. <glitch>...</glitch> World."
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], SentenceChunk("Hello.", False))
        self.assertEqual(chunks[1], SentenceChunk("World.", False))

    def test_ensure_trailing_punctuation(self):
        text = "Hello master <glitch>— PURGE —</glitch> back"
        chunks = split_sentences(text)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], SentenceChunk("Hello master.", False))
        self.assertEqual(chunks[1], SentenceChunk("— PURGE —.", True))
        self.assertEqual(chunks[2], SentenceChunk("back.", False))

    def test_empty_and_none(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences(None), [])

if __name__ == "__main__":
    unittest.main()
