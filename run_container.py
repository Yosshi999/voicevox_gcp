import argparse
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from typing import Optional

import soundfile
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import FileResponse

from voicevox_core import AccelerationMode, VoicevoxCore

kMoraLimit = 100
class TTSRequest(BaseModel):
    text: str
    speaker: int
    speed: float = 1.0

def b64encode_str(s):
    return base64.b64encode(s).decode("utf-8")


def generate_app(
    open_jtalk_dict_dir: Optional[Path] = None,
) -> FastAPI:
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
            open_jtalk_dict_dir=open_jtalk_dict_dir,
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
        text = body.text
        speaker = body.speaker
        
        query = app.vvcore.audio_query(text, speaker)
        query.volume_scale = 1.2
        query.pre_phoneme_length = 0.15
        query.post_phoneme_length = 0.1
        query.speed_scale = body.speed
        print(query)
        wave = app.vvcore.synthesis(query, speaker)

        with NamedTemporaryFile(delete=False) as f:
            f.write(wave)

        return FileResponse(f.name, media_type="audio/wav")


    @app.get("/hello", tags=["その他"])
    def hello() -> str:
        return "hello"

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50021)
    parser.add_argument("--open_jtalk_dict_dir", type=Path, default=None)

    args = parser.parse_args()

    uvicorn.run(
        generate_app(args.open_jtalk_dict_dir),
        host=args.host,
        port=args.port,
    )
