import wave
import math
import struct
import os

def generate_lock_sound():
    dirs = [
        "media/system_assets",
        "media/system_asstes"
    ]
    
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        
    sample_rate = 44100
    # Double blip: blip 1 (0.1s at 330Hz), silence (0.05s), blip 2 (0.15s at 261.63Hz)
    duration1 = 0.1
    silence = 0.05
    duration2 = 0.15
    
    num_samples1 = int(sample_rate * duration1)
    num_samples_silence = int(sample_rate * silence)
    num_samples2 = int(sample_rate * duration2)
    
    frames = bytearray()
    
    # Blip 1
    for i in range(num_samples1):
        t = float(i) / sample_rate
        amplitude = 25000 * (1.0 - (t / duration1))
        val = int(amplitude * math.sin(2.0 * math.pi * 330.0 * t))
        frames.extend(struct.pack('<h', val))
        
    # Silence
    for _ in range(num_samples_silence):
        frames.extend(struct.pack('<h', 0))
        
    # Blip 2
    for i in range(num_samples2):
        t = float(i) / sample_rate
        amplitude = 25000 * (1.0 - (t / duration2))
        val = int(amplitude * math.sin(2.0 * math.pi * 261.63 * t))
        frames.extend(struct.pack('<h', val))

    for d in dirs:
        filepath = os.path.join(d, "lock.mp3")
        with wave.open(filepath, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(frames)
        print(f"Generated {filepath} successfully.")

if __name__ == "__main__":
    generate_lock_sound()
