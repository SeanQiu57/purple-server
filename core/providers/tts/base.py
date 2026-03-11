import asyncio
from config.logger import setup_logging
import os
from abc import ABC, abstractmethod
from core.utils.tts import MarkdownCleaner
from core.utils.util import audio_to_data

TAG = __name__
logger = setup_logging()


class TTSProviderBase(ABC):
    def __init__(self, config, delete_audio_file):
        self.delete_audio_file = delete_audio_file
        self.output_file = config.get("output_dir")

    @abstractmethod
    def generate_filename(self):
        pass

    def to_tts(self, text):
        tmp_file = self.generate_filename()
        try:
            max_repeat_time = 5
            text = MarkdownCleaner.clean_markdown(text)
            success = False
            while max_repeat_time > 0:
                try:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                    asyncio.run(self.text_to_speak(text, tmp_file))
                    if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 0:
                        success = True
                        break
                    raise Exception("TTS output file missing or empty")
                except Exception as e:
                    err_msg = str(e)
                    logger.bind(tag=TAG).warning(
                        f"语音生成失败{5 - max_repeat_time + 1}次: {text}，错误: {err_msg}"
                    )
                    # 未执行成功，删除文件
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                    if "403" in err_msg and "Invalid response status" in err_msg:
                        logger.bind(tag=TAG).error(
                            "检测到TTS鉴权/地区限制(403)，停止重试"
                        )
                        break
                    max_repeat_time -= 1

            if success:
                logger.bind(tag=TAG).info(
                    f"语音生成成功: {text}:{tmp_file}，重试{5 - max_repeat_time}次"
                )
                return tmp_file
            else:
                logger.bind(tag=TAG).error(
                    f"语音生成失败: {text}，请检查网络或服务是否正常"
                )
                return None
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to generate TTS file: {e}")
            return None

    @abstractmethod
    async def text_to_speak(self, text, output_file):
        pass

    def audio_to_pcm_data(self, audio_file_path):
        """音频文件转换为PCM编码"""
        return audio_to_data(audio_file_path, is_opus=False)

    def audio_to_opus_data(self, audio_file_path):
        """音频文件转换为Opus编码"""
        return audio_to_data(audio_file_path, is_opus=True)
