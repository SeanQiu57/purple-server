# core/providers/asr/siliconflow_asr.py
import io
import os
import uuid
import wave
import time
import json
import requests
from typing import List, Optional, Tuple
from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
import numpy as np
import soundfile as sf
import tempfile


TAG = __name__
logger = setup_logging()

SILICONFLOW_TRANSCRIBE_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_MODEL = "FunAudioLLM/SenseVoiceSmall"  # 也可用 "TeleAI/TeleSpeechASR"

class ASRProvider(ASRProviderBase):
    """
    硅基流动 ASR（非流式、端点转写）
    - 输入：整段 PCM（上游 VAD 结束后）
    - 流程：PCM -> 封装 WAV -> multipart POST -> 返回 text
    """
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        self.api_key = config.get("api_key")  # 必填
        self.model = config.get("model", DEFAULT_MODEL)
        self.output_dir = config.get("output_dir", "tmp")
        self.delete_audio_file = delete_audio_file
        os.makedirs(self.output_dir, exist_ok=True)

        if not self.api_key:
            raise ValueError("SiliconFlow ASR 需要 api_key，请在 data/.config.yaml 配置 asr.siliconflow.api_key")

    # ---- PCM / WAV 工具 ----
    def _pcm_to_wav_bytes(self, pcm_frames: List[bytes], sample_rate=16000, sampwidth=2, channels=1) -> bytes:
        """把 PCM 帧封装成 WAV（二进制）"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(pcm_frames))
        return buf.getvalue()

    def _save_wav_file(self, wav_bytes: bytes, session_id: str) -> str:
        """可选：把 WAV 落盘，便于排查问题"""
        file_name = f"asr_siliconflow_{session_id}_{uuid.uuid4().hex}.wav"
        file_path = os.path.join(self.output_dir, file_name)
        with open(file_path, "wb") as f:
            f.write(wav_bytes)
        logger.bind(tag=TAG).debug(f"音频文件已保存至: {file_path}")
        return file_path

    # ---- HTTP 调用 ----
    def _http_transcribe(self, wav_bytes: bytes) -> Optional[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {
            "file": ("audio.wav", wav_bytes, "audio/wav"),
        }
        # 仅必填参数：model；可按需扩展 temperature 等
        data = {
            "model": self.model,
        }
        try:
            resp = requests.post(
                SILICONFLOW_TRANSCRIBE_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=90,
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"HTTP 请求失败: {e}", exc_info=True)
            return None

        if resp.status_code != 200:
            # 常见：401(鉴权), 429(限流), 5xx(服务端)
            body = resp.text[:500]
            logger.bind(tag=TAG).error(f"SiliconFlow ASR HTTP {resp.status_code}: {body}")
            return None

        try:
            js = resp.json()
        except Exception:
            logger.bind(tag=TAG).error(f"响应非 JSON：{resp.text[:500]}")
            return None

        text = js.get("text")
        if text is None:
            logger.bind(tag=TAG).error(f"响应无 text 字段：{json.dumps(js)[:500]}")
            return None
        return text

    def _array_to_wav_bytes(self, arr: np.ndarray, sr: int = 16000) -> bytes:
        """直接把 numpy float32 写成 wav bytes"""
        if arr.ndim > 1:
            arr = arr[:, 0]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            sf.write(f.name, arr, sr, subtype="PCM_16")
            f.seek(0)
            wav_bytes = f.read()
        os.remove(f.name)
        return wav_bytes

    async def speech_to_text(self, audio, session_id: str):
        """
        兼容两种输入：
        - List[bytes]（老的 opus 流）
        - np.ndarray（新的 VAD segment）
        """
        file_path = None
        try:
            if isinstance(audio, list):  # 旧的 opus 流程
                if self.audio_format == "pcm":
                    pcm_frames = audio
                else:
                    pcm_frames = self.decode_opus(audio)
                wav_bytes = self._pcm_to_wav_bytes(pcm_frames)

            elif isinstance(audio, np.ndarray):  # 新的 float32 PCM
                wav_bytes = self._array_to_wav_bytes(audio)

            else:
                raise TypeError(f"不支持的输入类型: {type(audio)}")

            # 可选落盘
            if not self.delete_audio_file:
                file_path = self._save_wav_file(wav_bytes, session_id)

            # 调用 ASR HTTP
            t0 = time.time()
            text = self._http_transcribe(wav_bytes)
            dt = time.time() - t0

            if text is None:
                logger.bind(tag=TAG).error("SiliconFlow ASR 识别失败")
                return "", file_path

            logger.bind(tag=TAG).info(f"SiliconFlow ASR ok in {dt:.2f}s, len={len(text)}")
            return text, file_path

        except Exception as e:
            logger.bind(tag=TAG).error(f"SiliconFlow ASR 异常: {e}", exc_info=True)
            return "", file_path
