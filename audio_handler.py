import logging

logger = logging.getLogger(__name__)


class AudioHandler:
    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 bits_per_sample: int = 16, frame_ms: int = 10):
        self.sample_rate = sample_rate
        self.channels = channels
        self.bits_per_sample = bits_per_sample
        self.frame_ms = frame_ms

    @property
    def bytes_per_sample(self) -> int:
        return self.bits_per_sample // 8

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * self.bytes_per_sample * self.channels * (self.frame_ms / 1000))

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.bytes_per_sample * self.channels

    def duration_ms(self, data: bytes) -> float:
        if not data:
            return 0.0
        frames = len(data) / self.frame_bytes
        return frames * self.frame_ms

    def split_frames(self, data: bytes, max_frame_count: int = 100) -> list[bytes]:
        frames = []
        for i in range(0, len(data), self.frame_bytes):
            frame = data[i:i + self.frame_bytes]
            if len(frame) == self.frame_bytes:
                frames.append(frame)
            if len(frames) >= max_frame_count:
                break
        return frames

    def merge(self, *chunks: bytes) -> bytes:
        return b"".join(chunks)

    def validate(self, data: bytes) -> bool:
        return len(data) % self.frame_bytes == 0

    def trim_to_frame(self, data: bytes) -> bytes:
        remainder = len(data) % self.frame_bytes
        if remainder != 0:
            return data[:-remainder]
        return data

    def pcm_to_wav_header(self, data_len: int) -> bytes:
        byte_rate = self.sample_rate * self.channels * self.bytes_per_sample
        block_align = self.channels * self.bytes_per_sample
        header = bytearray(44)
        header[0:4] = b"RIFF"
        header[4:8] = (data_len + 36).to_bytes(4, "little")
        header[8:12] = b"WAVE"
        header[12:16] = b"fmt "
        header[16:20] = (16).to_bytes(4, "little")
        header[20:22] = (1).to_bytes(2, "little")
        header[22:24] = self.channels.to_bytes(2, "little")
        header[24:28] = self.sample_rate.to_bytes(4, "little")
        header[28:32] = byte_rate.to_bytes(4, "little")
        header[32:34] = block_align.to_bytes(2, "little")
        header[34:36] = self.bits_per_sample.to_bytes(2, "little")
        header[36:40] = b"data"
        header[40:44] = data_len.to_bytes(4, "little")
        return bytes(header)

    def wrap_wav(self, pcm_data: bytes) -> bytes:
        return self.pcm_to_wav_header(len(pcm_data)) + pcm_data
