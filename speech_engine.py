"""
Speech recognition engine using Vosk for real-time speech-to-text.
"""

import json
import queue
import threading
import struct
from pathlib import Path
from typing import Callable, Optional

import pyaudio
from vosk import Model, KaldiRecognizer, SetLogLevel

# Suppress Vosk logging
SetLogLevel(-1)

# Audio configuration
VOSK_SAMPLE_RATE = 16000  # Vosk requires 16000 Hz
CHUNK_DURATION_MS = 250   # Chunk duration in milliseconds


class SpeechEngine:
    """Real-time speech recognition using Vosk."""

    def __init__(self, model_path: str, device_index: Optional[int] = None):
        """
        Initialize the speech engine.
        
        Args:
            model_path: Path to the Vosk model directory
            device_index: PyAudio device index for microphone (None for default)
        """
        self.model_path = Path(model_path)
        self.device_index = device_index
        self.model: Optional[Model] = None
        self.recognizer: Optional[KaldiRecognizer] = None
        self.audio: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue = queue.Queue()
        
        # Audio settings - will be determined from device
        self._device_sample_rate: int = VOSK_SAMPLE_RATE
        self._chunk_size: int = 4000
        
        # Callbacks
        self._on_partial: Optional[Callable[[str], None]] = None
        self._on_result: Optional[Callable[[str], None]] = None
        self._on_words: Optional[Callable[[list], None]] = None

    def load_model(self) -> bool:
        """Load the Vosk model. Returns True if successful."""
        if not self.model_path.exists():
            print(f"Model not found at {self.model_path}")
            return False
        
        try:
            self.model = Model(str(self.model_path))
            self.recognizer = KaldiRecognizer(self.model, VOSK_SAMPLE_RATE)
            self.recognizer.SetWords(True)  # Enable word-level timestamps
            return True
        except Exception as e:
            print(f"Error loading model: {e}")
            return False

    def list_devices(self) -> list:
        """List available audio input devices."""
        if self.audio is None:
            self.audio = pyaudio.PyAudio()
        
        devices = []
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:  # Input device
                devices.append({
                    'index': i,
                    'name': info['name'],
                    'channels': info['maxInputChannels'],
                    'sample_rate': int(info['defaultSampleRate'])
                })
        return devices

    def find_best_device(self) -> Optional[int]:
        """
        Find the best audio input device, preferring PipeWire.
        
        Returns:
            Device index or None to use system default
        """
        if self.audio is None:
            self.audio = pyaudio.PyAudio()
        
        # Priority order: pipewire > default > first available
        pipewire_idx = None
        default_idx = None
        
        for i in range(self.audio.get_device_count()):
            info = self.audio.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                name = info['name'].lower()
                if name == 'pipewire':
                    pipewire_idx = i
                elif name == 'default':
                    default_idx = i
        
        if pipewire_idx is not None:
            return pipewire_idx
        if default_idx is not None:
            return default_idx
        return None  # Use system default

    def set_device(self, device_index: int):
        """Set the audio input device."""
        self.device_index = device_index

    def on_partial(self, callback: Callable[[str], None]):
        """Set callback for partial recognition results."""
        self._on_partial = callback

    def on_result(self, callback: Callable[[str], None]):
        """Set callback for final recognition results."""
        self._on_result = callback

    def on_words(self, callback: Callable[[list], None]):
        """Set callback for word-level results with timing."""
        self._on_words = callback

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback - receives audio data."""
        if self._running:
            try:
                self._audio_queue.put_nowait(in_data)
            except:
                pass  # Drop frame if queue is full
        return (None, pyaudio.paContinue)

    def _resample(self, data: bytes, from_rate: int, to_rate: int) -> bytes:
        """
        Simple linear interpolation resampling.
        
        Args:
            data: Raw 16-bit PCM audio data
            from_rate: Source sample rate
            to_rate: Target sample rate
            
        Returns:
            Resampled audio data
        """
        if from_rate == to_rate:
            return data
        
        # Convert bytes to samples
        samples = struct.unpack(f'<{len(data)//2}h', data)
        
        # Calculate resampling ratio
        ratio = to_rate / from_rate
        new_length = int(len(samples) * ratio)
        
        # Linear interpolation resampling
        resampled = []
        for i in range(new_length):
            src_idx = i / ratio
            idx = int(src_idx)
            frac = src_idx - idx
            
            if idx + 1 < len(samples):
                sample = samples[idx] * (1 - frac) + samples[idx + 1] * frac
            else:
                sample = samples[idx] if idx < len(samples) else 0
            
            resampled.append(int(sample))
        
        # Convert back to bytes
        return struct.pack(f'<{len(resampled)}h', *resampled)

    def _recognition_thread(self):
        """Background thread for processing audio and recognition."""
        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            
            # Resample if needed
            if self._device_sample_rate != VOSK_SAMPLE_RATE:
                data = self._resample(data, self._device_sample_rate, VOSK_SAMPLE_RATE)

            if self.recognizer.AcceptWaveform(data):
                # Final result for this utterance
                result = json.loads(self.recognizer.Result())
                text = result.get('text', '')
                
                if text and self._on_result:
                    self._on_result(text)
                
                # Word-level results with timing
                if 'result' in result and self._on_words:
                    self._on_words(result['result'])
            else:
                # Partial result
                partial = json.loads(self.recognizer.PartialResult())
                text = partial.get('partial', '')
                
                if text and self._on_partial:
                    self._on_partial(text)

    def start(self) -> bool:
        """Start speech recognition. Returns True if successful."""
        if self._running:
            return True

        if self.model is None:
            if not self.load_model():
                return False

        if self.audio is None:
            self.audio = pyaudio.PyAudio()

        try:
            # Auto-detect best device if not specified
            if self.device_index is None:
                self.device_index = self.find_best_device()
            
            # Get device info to determine sample rate
            if self.device_index is not None:
                device_info = self.audio.get_device_info_by_index(self.device_index)
            else:
                device_info = self.audio.get_default_input_device_info()
            
            self._device_sample_rate = int(device_info['defaultSampleRate'])
            
            # Calculate chunk size for desired duration at device sample rate
            self._chunk_size = int(self._device_sample_rate * CHUNK_DURATION_MS / 1000)
            
            print(f"Using audio device: {device_info['name']}")
            print(f"Device sample rate: {self._device_sample_rate} Hz")
            print(f"Resampling to: {VOSK_SAMPLE_RATE} Hz")
            
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._device_sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self._chunk_size,
                stream_callback=self._audio_callback
            )
            
            self._running = True
            self._thread = threading.Thread(target=self._recognition_thread, daemon=True)
            self._thread.start()
            
            self.stream.start_stream()
            return True
            
        except Exception as e:
            print(f"Error starting audio stream: {e}")
            return False

    def stop(self):
        """Stop speech recognition."""
        self._running = False
        
        # Clear the queue first to unblock the recognition thread
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break
        
        # Stop the stream
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception as e:
                print(f"Error stopping stream: {e}")
            self.stream = None
        
        # Wait for thread with timeout
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def reset(self):
        """Reset the recognizer state."""
        if self.recognizer:
            # Create a fresh recognizer
            self.recognizer = KaldiRecognizer(self.model, VOSK_SAMPLE_RATE)
            self.recognizer.SetWords(True)

    def cleanup(self):
        """Clean up resources."""
        self.stop()
        if self.audio:
            self.audio.terminate()
            self.audio = None


# Convenience function for testing
def main():
    """Test the speech engine."""
    import sys
    
    # Find model
    model_paths = [
        "models/vosk-model-small-en-us-0.15",
        "models/vosk-model-en-us-0.22",
    ]
    
    model_path = None
    for path in model_paths:
        if Path(path).exists():
            model_path = path
            break
    
    if not model_path:
        print("No Vosk model found. Please download one to the models/ directory.")
        print("Example: wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip")
        sys.exit(1)
    
    engine = SpeechEngine(model_path)
    
    # List devices
    print("Available audio devices:")
    for dev in engine.list_devices():
        print(f"  [{dev['index']}] {dev['name']}")
    
    print(f"\nLoading model from {model_path}...")
    if not engine.load_model():
        sys.exit(1)
    
    print("Model loaded. Starting recognition (Ctrl+C to stop)...")
    
    def on_partial(text):
        print(f"\rPartial: {text}", end='', flush=True)
    
    def on_result(text):
        print(f"\nFinal: {text}")
    
    def on_words(words):
        for w in words:
            print(f"  Word: '{w['word']}' ({w['start']:.2f}s - {w['end']:.2f}s)")
    
    engine.on_partial(on_partial)
    engine.on_result(on_result)
    engine.on_words(on_words)
    
    if not engine.start():
        print("Failed to start recognition")
        sys.exit(1)
    
    try:
        while True:
            import time
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        engine.cleanup()


if __name__ == "__main__":
    main()



