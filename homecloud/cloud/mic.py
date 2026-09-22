import sounddevice as sd
from scipy.io.wavfile import write

fs = 44100
seconds = 3

print("開始錄音")
audio = sd.rec(int(seconds * fs), samplerate=fs, channels=1)
sd.wait()
write('test.wav', fs, audio)

print("完成，播放看看")