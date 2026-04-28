import base64

# Simulating base64 decoding in frontend
wav_bytes = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00'
b64_data = base64.b64encode(wav_bytes).decode('utf-8')
print(b64_data)
# Frontend format: data:audio/wav;base64,
data_url = f"data:audio/wav;base64,{b64_data}"
print(data_url)
