import numpy as np
import wave
import struct

def create_test_audio(filename="audio/test_audio.wav", duration=5, frequency=440):
    """테스트용 .wav 파일 생성"""
    
    # 샘플 레이트
    sample_rate = 44100
    
    # 샘플 수
    num_samples = duration * sample_rate
    
    # 신호 생성 (순수 음파)
    t = np.linspace(0, duration, num_samples)
    signal = np.sin(2 * np.pi * frequency * t)
    
    # 음량 조정 (0-32767 범위)
    signal = (signal * 32767).astype(np.int16)
    
    # WAV 파일로 저장
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1)  # 모노
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(signal.tobytes())
    
    print(f"Test audio created: {filename}")

if __name__ == "__main__":
    create_test_audio()