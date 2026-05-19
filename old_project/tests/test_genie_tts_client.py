from voice_satellite.genie_tts import encode_pcm_chunk, iter_complete_sentences


def main() -> None:
    sentences, pending = iter_complete_sentences([], "Hello there. How")
    assert sentences == ["Hello there."]
    assert pending == ["How"]

    sentences, pending = iter_complete_sentences(pending, " are you? Fine")
    assert sentences == ["How are you?"]
    assert pending == ["Fine"]
    
    # Test glitch tag extraction
    sentences, pending = iter_complete_sentences([], "Ordis will <glitch>— PURGE THEM ALL —</glitch> uh, Ordis")
    assert sentences == ["Ordis will", "<glitch>— PURGE THEM ALL —</glitch>"]
    assert pending == [" uh, Ordis"]

    assert encode_pcm_chunk(b"\x00\x00") == "AAA="
    print("genie tts client tests passed")


if __name__ == "__main__":
    main()
