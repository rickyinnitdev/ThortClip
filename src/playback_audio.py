"""Align Windows playback packets to the recorder timeline before piping PCM to FFmpeg."""
import math
import threading
import time
from collections import deque

import pyaudiowpatch as pa


class AudioTimeline:
    def __init__(self, rate, channels):
        self.rate = rate
        self.channels = channels
        self.packets = deque(maxlen=200)
        self.next_frame = None
        self.input_latency = 0.0

    def capture(self, data, frames, timing, flags):
        packet_time = timing["input_buffer_adc_time"]
        now = timing["current_time"]
        delta = packet_time - now
        if not packet_time or not math.isfinite(delta) or abs(delta) > 1:
            delta = -max(frames / self.rate, self.input_latency)
        position = round((time.perf_counter() - self.origin + delta) * self.rate)
        # Adjacent device packets contain contiguous PCM. Callback scheduling jitter
        # must not splice zeros into the waveform or discard parts of each packet.
        if self.next_frame is not None and not (flags & pa.paInputOverflow):
            if abs(position - self.next_frame) < self.rate // 10:
                position = self.next_frame
        self.next_frame = position + frames
        self.packets.append((position, data))
        return None, pa.paContinue

    def read(self, cursor, end):
        frame_bytes = self.channels * 2
        block = bytearray((end - cursor) * frame_bytes)
        while self.packets:
            start, data = self.packets[0]
            if start >= end:
                break
            stop = start + len(data) // frame_bytes
            lo, hi = max(cursor, start), min(end, stop)
            if hi > lo:
                block[(lo-cursor)*frame_bytes:(hi-cursor)*frame_bytes] = data[(lo-start)*frame_bytes:(hi-start)*frame_bytes]
            if stop > end:
                break
            self.packets.popleft()
        return block


class PlaybackAudio:
    def __init__(self,
                 microphone: bool = False,
                 playback_device: str = "default",
                 microphone_device: str = "default",
                 playback_volume: float = 1.0,
                 microphone_volume: float = 1.0,
                 game_audio_enabled: bool = True,
                 audio_device: str = "system",
                 **kwargs):
        self.host = pa.PyAudio()
        self.mic_stream = None
        self.mic_warning = ""
        self.done = threading.Event()
        self.thread = None
        self.playback_volume = max(0.0, float(playback_volume))
        self.microphone_volume = max(0.0, float(microphone_volume))
        self.game_audio_enabled = bool(game_audio_enabled)

        try:
            device = None
            if playback_device and playback_device.lower() != "default":
                for i in range(self.host.get_device_count()):
                    d = self.host.get_device_info_by_index(i)
                    if d.get("isLoopbackDevice") and playback_device in d["name"]:
                        device = d
                        break
            if device is None:
                device = self.host.get_default_wasapi_loopback()

            self.rate = int(device["defaultSampleRate"])
            self.channels = device["maxInputChannels"]
            self.chunk = self.rate // 100
            self.game = AudioTimeline(self.rate, self.channels)
            self.stream = self.host.open(format=pa.paInt16, channels=self.channels,
                                         rate=self.rate, input=True, input_device_index=device["index"],
                                         frames_per_buffer=self.chunk, start=False,
                                         stream_callback=self.game.capture)
            self.game.input_latency = self.stream.get_input_latency()
        except Exception:
            self.host.terminate()
            raise

        if microphone:
            try:
                mic_dev = None
                if microphone_device and microphone_device.lower() != "default":
                    wasapi_info = self.host.get_host_api_info_by_type(pa.paWASAPI)
                    w_idx = wasapi_info["index"]
                    for i in range(self.host.get_device_count()):
                        d = self.host.get_device_info_by_index(i)
                        if d["hostApi"] == w_idx and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                            if microphone_device in d["name"]:
                                mic_dev = d
                                break
                if mic_dev is None:
                    mic_dev = self.host.get_default_wasapi_device(d_in=True)

                self.mic = AudioTimeline(self.rate, min(2, mic_dev["maxInputChannels"]))
                self.mic_stream = self.host.open(
                    format=pa.paInt16, channels=self.mic.channels, rate=self.rate,
                    input=True, input_device_index=mic_dev["index"], frames_per_buffer=self.chunk,
                    start=False, stream_callback=self.mic.capture)
                self.mic.input_latency = self.mic_stream.get_input_latency()
            except (OSError, ValueError, LookupError) as exc:
                self.mic_warning = f"Microphone unavailable. Recording game audio only. ({exc})"

    def start(self, pipe, origin):
        self.origin = self.game.origin = origin
        if self.mic_stream:
            self.mic.origin = origin
            try:
                self.mic_stream.start_stream()
            except OSError as exc:
                self.mic_warning = f"Microphone unavailable. Recording game audio only. ({exc})"
                self.mic_stream.close()
                self.mic_stream = None
        self.stream.start_stream()
        self.thread = threading.Thread(target=self.feed, args=(pipe,), daemon=True)
        self.thread.start()

    def feed(self, pipe):
        import numpy as np
        cursor = 0
        try:
            while not self.done.is_set():
                # Allow packets to arrive before filling genuinely silent intervals.
                ready = int((time.perf_counter() - self.origin - 0.08) * self.rate)
                if ready < cursor + self.chunk:
                    self.done.wait(0.01)
                    continue
                end = cursor + self.chunk
                block = self.game.read(cursor, end)

                if not self.game_audio_enabled:
                    game = np.zeros(len(block) // 2, dtype=np.float32).reshape(-1, self.channels)
                else:
                    game = np.frombuffer(block, dtype="<i2").reshape(-1, self.channels).astype(np.float32)
                    if self.playback_volume != 1.0:
                        game = game * self.playback_volume

                if self.mic_stream:
                    mic = np.frombuffer(self.mic.read(cursor, end), dtype="<i2")
                    voice = mic.reshape(-1, self.mic.channels).mean(axis=1).astype(np.float32)
                    if self.microphone_volume != 1.0:
                        voice = voice * self.microphone_volume
                    mixed = game + voice[:, None]
                    block = np.clip(mixed, -32768, 32767).astype("<i2").tobytes()
                else:
                    block = np.clip(game, -32768, 32767).astype("<i2").tobytes()

                pipe.write(block)
                pipe.flush()
                cursor = end
        except OSError:
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def close(self):
        self.done.set()
        if self.thread:
            self.thread.join()
        if self.mic_stream:
            self.mic_stream.close()
        self.stream.close()
        self.host.terminate()
