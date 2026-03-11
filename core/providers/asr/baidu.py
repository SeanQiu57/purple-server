import base64
import io
import os
import time
import uuid
from typing import Optional, Tuple, List
import wave
from openai import OpenAI

from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool = True):
        super().__init__()
        self.api_key = config.get("api_key")
        self.base_url = config.get(
            "base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = config.get("model", "qwen3-asr-flash")
        self.language = config.get("language")
        self.enable_itn = bool(config.get("enable_itn", False))
        self.request_timeout = int(config.get("request_timeout", 90))

        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.request_timeout,
        )

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

    def save_audio_to_file(self, pcm_data: List[bytes], session_id: str) -> str:
        """PCM数据保存为WAV文件"""
        module_name = __name__.split(".")[-1]
        file_name = f"asr_{module_name}_{session_id}_{uuid.uuid4()}.wav"
        file_path = os.path.join(self.output_dir, file_name)

        with wave.open(file_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 2 bytes = 16-bit
            wf.setframerate(16000)
            wf.writeframes(b"".join(pcm_data))

        return file_path

    @staticmethod
    def _pcm_to_wav_bytes(pcm_data: List[bytes]) -> bytes:
        """PCM数据转换为WAV二进制"""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 2 bytes = 16-bit
            wf.setframerate(16000)
            wf.writeframes(b"".join(pcm_data))
        return buffer.getvalue()

    @staticmethod
    def _extract_text_from_completion(completion) -> Optional[str]:
        """从OpenAI SDK对象中提取文本"""
        try:
            choices = completion.choices or []
            if not choices:
                return None

            message = choices[0].message
            content = getattr(message, "content", None)

            if isinstance(content, str):
                return content.strip()

            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                    else:
                        text = getattr(item, "text", None)
                    if text:
                        texts.append(str(text))
                if texts:
                    return "".join(texts).strip()
        except Exception:
            return None

        return None

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """将语音数据转换为文本"""
        if not opus_data:
            logger.bind(tag=TAG).warning("音频数据为空！")
            return "", None

        file_path = None
        try:
            # 检查配置是否已设置
            if not self.api_key:
                logger.bind(tag=TAG).error("DashScope API Key未设置，无法进行识别")
                return None, file_path

            # 将Opus音频数据解码为PCM
            if self.audio_format == "pcm":
                pcm_data = opus_data
            else:
                pcm_data = self.decode_opus(opus_data)

            # 判断是否保存为WAV文件
            if self.delete_audio_file:
                pass
            else:
                file_path = self.save_audio_to_file(pcm_data, session_id)

            wav_bytes = self._pcm_to_wav_bytes(pcm_data)
            audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
            audio_data_uri = f"data:audio/wav;base64,{audio_base64}"

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": audio_data_uri},
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {
                    "enable_itn": self.enable_itn,
                },
            }
            if self.language:
                payload["asr_options"]["language"] = self.language

            start_time = time.time()
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=payload["messages"],
                stream=False,
                extra_body={"asr_options": payload["asr_options"]},
            )
            text_result = self._extract_text_from_completion(completion)

            if text_result:
                logger.bind(tag=TAG).debug(
                    f"DashScope语音识别耗时: {time.time() - start_time:.3f}s | 响应ID: {getattr(completion, 'id', None)}"
                )
                return text_result, file_path
            else:
                raise Exception(
                    f"DashScope语音识别返回为空，响应ID: {getattr(completion, 'id', None)}"
                )

        except Exception as e:
            logger.bind(tag=TAG).error("处理音频时发生错误！{}", e, exc_info=True)
            # 返回空字符串，避免后续文本处理出现 NoneType 异常
            return "", file_path
