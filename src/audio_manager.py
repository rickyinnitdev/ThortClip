"""WASAPI audio device discovery and test utilities."""
from typing import List, Optional
import pyaudiowpatch as pa
import numpy as np


def list_playback_devices() -> List[str]:
    """Returns clean names of available WASAPI loopback (speaker/headphone) devices."""
    devices = []
    try:
        p = pa.PyAudio()
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d.get("isLoopbackDevice"):
                name = d["name"].replace(" [Loopback]", "").strip()
                if name and name not in devices:
                    devices.append(name)
        p.terminate()
    except Exception as exc:
        print(f"[AudioManager] Error listing playback devices: {exc}")
    return devices


def list_microphone_devices() -> List[str]:
    """Returns clean names of available WASAPI microphone/input devices."""
    devices = []
    try:
        p = pa.PyAudio()
        wasapi_info = p.get_host_api_info_by_type(pa.paWASAPI)
        w_idx = wasapi_info["index"]
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] == w_idx and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                name = d["name"].strip()
                if name and name not in devices:
                    devices.append(name)
        p.terminate()
    except Exception as exc:
        print(f"[AudioManager] Error listing microphone devices: {exc}")
    return devices


def test_microphone_level(device_name: str = "default", duration_sec: float = 0.5) -> float:
    """Samples microphone input for duration_sec and returns peak level from 0.0 to 1.0."""
    p = pa.PyAudio()
    stream = None
    try:
        target_dev = None
        if device_name and device_name.lower() != "default":
            wasapi_info = p.get_host_api_info_by_type(pa.paWASAPI)
            w_idx = wasapi_info["index"]
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if d["hostApi"] == w_idx and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                    if device_name in d["name"]:
                        target_dev = d
                        break

        if target_dev is None:
            target_dev = p.get_default_wasapi_device(d_in=True)

        rate = int(target_dev["defaultSampleRate"])
        channels = min(2, target_dev["maxInputChannels"])
        chunk = 1024
        stream = p.open(
            format=pa.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=target_dev["index"],
            frames_per_buffer=chunk
        )
        stream.start_stream()

        num_chunks = max(1, int((rate * duration_sec) / chunk))
        peaks = []
        for _ in range(num_chunks):
            data = stream.read(chunk, exception_on_overflow=False)
            arr = np.frombuffer(data, dtype=np.int16)
            if len(arr) > 0:
                peaks.append(np.max(np.abs(arr)))

        peak_val = max(peaks) / 32768.0 if peaks else 0.0
        return min(1.0, float(peak_val))
    except Exception as exc:
        print(f"[AudioManager] Microphone test error: {exc}")
        return 0.0
    finally:
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        p.terminate()
