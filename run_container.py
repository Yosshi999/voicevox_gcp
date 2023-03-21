from dataclasses import dataclass
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
import time
from typing import Optional

from omegaconf import OmegaConf
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import FileResponse

from voicevox_core import AccelerationMode, VoicevoxCore


@dataclass
class AppConfig:
    uvicorn_host: str = "0.0.0.0"
    uvicorn_port: int = os.environ.get("PORT", 50021)

    open_jtalk_dict_dir: str = "/opt/voicevox_engine/dic/open_jtalk_dic_utf_8-1.11"
    # The number of threads for ONNX Runtime. Default value 0 means AUTO.
    threads: int = os.environ.get("THREADS", 0)
    base_speed_scale: float = os.environ.get("BASE_SPEED_SCALE", 1.0)
    volume_scale: float = os.environ.get("VOLUME_SCALE", 1.2)
    pre_phoneme_length: float = os.environ.get("PRE_PHONEME_LENGTH", 0.15)
    post_phoneme_length: float = os.environ.get("POST_PHONEME_LENGTH", 0.1)

class TTSRequest(BaseModel):
    text: str
    speaker: int
    speed: float = 1.0

def b64encode_str(s):
    return base64.b64encode(s).decode("utf-8")


def generate_app(conf: AppConfig) -> FastAPI:
    app = FastAPI(
        title="VOICEVOX ENGINE",
        description="VOICEVOXの音声合成エンジンです。",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def start_core():
        app.vvcore = VoicevoxCore(
            acceleration_mode=AccelerationMode("AUTO"),
            cpu_num_threads=conf.threads,
            open_jtalk_dict_dir=conf.open_jtalk_dict_dir,
            load_all_models=True)

    @app.post(
        "/tts",
        response_class=FileResponse,
        responses={
            200: {
                "content": {
                    "audio/wav": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
        tags=["音声合成"],
        summary="音声合成する",
    )
    def tts(body: TTSRequest):
        tic = time.perf_counter()
        text = body.text
        speaker = body.speaker
        
        query = app.vvcore.audio_query(text, speaker)
        query.volume_scale = conf.volume_scale
        query.pre_phoneme_length = conf.pre_phoneme_length
        query.post_phoneme_length = conf.post_phoneme_length
        query.speed_scale = body.speed * conf.base_speed_scale
        print(body.text, ":", query)
        wave = app.vvcore.synthesis(query, speaker)

        with NamedTemporaryFile(delete=False) as f:
            f.write(wave)

        # stat
        moras = 0
        speech_length = query.pre_phoneme_length + query.post_phoneme_length
        for phrase in query.accent_phrases:
            moras += len(phrase.moras)
            for m in phrase.moras:
                if m.consonant_length is not None:
                    speech_length += m.consonant_length
                if m.vowel_length is not None:
                    speech_length += m.vowel_length
        speech_length /= query.speed_scale

        toc = time.perf_counter()
        proctime = toc - tic
        print("PERF", f"moras={moras}", f"wavtime={speech_length:.3f}", f"proctime={proctime:.3f}", f"genrate={speech_length / proctime}", f"text={query.kana}")

        return FileResponse(f.name, media_type="audio/wav")


    @app.get("/hello", tags=["その他"])
    def hello() -> str:
        return "hello"

    return app


if __name__ == "__main__":
    conf = OmegaConf.structured(AppConfig())
    print(OmegaConf.to_yaml(conf))

    uvicorn.run(
        generate_app(conf),
        host=conf.uvicorn_host,
        port=conf.uvicorn_port,
    )
