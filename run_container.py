import argparse
import base64
import multiprocessing
import io
import os
import sys
import zipfile
from distutils.version import LooseVersion
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from typing import Dict, List, Optional

import numpy as np
import soundfile
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import FileResponse

from voicevox_engine import __version__
from voicevox_engine.full_context_label import extract_full_context_label, pyopenjtalk
from voicevox_engine.kana_parser import create_kana, parse_kana
from voicevox_engine.model import (
    AccentPhrase,
    AudioQuery,
    Mora,
    ParseKanaBadRequest,
    ParseKanaError,
    Speaker,
)
from voicevox_engine.mora_list import openjtalk_mora2text
from voicevox_engine.synthesis_engine import SynthesisEngineBase, make_synthesis_engines
from voicevox_engine.user_dict import user_dict_startup_processing
from voicevox_engine.utility import engine_root

kMoraLimit = 100
enable_interrogative_upspeak = True

class TTSRequest(BaseModel):
    text: str
    speaker: int
    speed: float = 1.0

def mora_to_text(mora: str):
    if mora[-1:] in ["A", "I", "U", "E", "O"]:
        # 無声化母音を小文字に
        mora = mora[:-1] + mora[-1].lower()
    if mora in openjtalk_mora2text:
        return openjtalk_mora2text[mora]
    else:
        return mora


def generate_app(
    synthesis_engines: Dict[str, SynthesisEngineBase], latest_core_version: str
) -> FastAPI:
    root_dir = engine_root()

    default_sampling_rate = synthesis_engines[latest_core_version].default_sampling_rate

    app = FastAPI(
        title="VOICEVOX ENGINE",
        description="VOICEVOXの音声合成エンジンです。",
        version=__version__,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def apply_user_dict():
        user_dict_startup_processing(compiled_dict_path=Path("/opt/voicevox_engine/user.dic"))

    def get_engine(core_version: Optional[str]) -> SynthesisEngineBase:
        if core_version is None:
            return synthesis_engines[latest_core_version]
        if core_version in synthesis_engines:
            return synthesis_engines[core_version]
        raise HTTPException(status_code=422, detail="不明なバージョンです")

    # def replace_mora_data(
    #     accent_phrases: List[AccentPhrase], speaker_id: int
    # ) -> List[AccentPhrase]:
    #     return engine.replace_mora_pitch(
    #         accent_phrases=engine.replace_phoneme_length(
    #             accent_phrases=accent_phrases,
    #             speaker_id=speaker_id,
    #         ),
    #         speaker_id=speaker_id,
    #     )

    def create_accent_phrases(text: str, speaker_id: int) -> List[AccentPhrase]:
        if len(text.strip()) == 0:
            return []

        utterance = extract_full_context_label(text)
        if len(utterance.breath_groups) == 0:
            return []

        return replace_mora_data(
            accent_phrases=[
                AccentPhrase(
                    moras=[
                        Mora(
                            text=mora_to_text(
                                "".join([p.phoneme for p in mora.phonemes])
                            ),
                            consonant=(
                                mora.consonant.phoneme
                                if mora.consonant is not None
                                else None
                            ),
                            consonant_length=0 if mora.consonant is not None else None,
                            vowel=mora.vowel.phoneme,
                            vowel_length=0,
                            pitch=0,
                        )
                        for mora in accent_phrase.moras
                    ],
                    accent=accent_phrase.accent,
                    pause_mora=(
                        Mora(
                            text="、",
                            consonant=None,
                            consonant_length=None,
                            vowel="pau",
                            vowel_length=0,
                            pitch=0,
                        )
                        if (
                            i_accent_phrase == len(breath_group.accent_phrases) - 1
                            and i_breath_group != len(utterance.breath_groups) - 1
                        )
                        else None
                    ),
                )
                for i_breath_group, breath_group in enumerate(utterance.breath_groups)
                for i_accent_phrase, accent_phrase in enumerate(
                    breath_group.accent_phrases
                )
            ],
            speaker_id=speaker_id,
        )

    def decode_base64_waves(waves: List[str]):
        if len(waves) == 0:
            raise HTTPException(status_code=422, detail="wavファイルが含まれていません")

        waves_nparray = []
        for i in range(len(waves)):
            try:
                wav_bin = base64.standard_b64decode(waves[i])
            except ValueError:
                raise HTTPException(status_code=422, detail="base64デコードに失敗しました")
            try:
                _data, _sampling_rate = soundfile.read(io.BytesIO(wav_bin))
            except Exception:
                raise HTTPException(status_code=422, detail="wavファイルを読み込めませんでした")
            if i == 0:
                sampling_rate = _sampling_rate
                channels = _data.ndim
            else:
                if sampling_rate != _sampling_rate:
                    raise HTTPException(status_code=422, detail="ファイル間でサンプリングレートが異なります")
                if channels != _data.ndim:
                    if channels == 1:
                        _data = _data.T[0]
                    else:
                        _data = np.array([_data, _data]).T
            waves_nparray.append(_data)

        return waves_nparray, sampling_rate

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
        engine = get_engine(core_version=None)
        accent_phrases = engine.create_accent_phrases(text, speaker_id=speaker)
        trunc = []
        mora_length = 0
        for accent in accent_phrases:
            mora_length += len(accent.moras)
            if mora_length > kMoraLimit:
                break
            trunc.append(accent)
        print("[%d moras] %s..." % (mora_length, body.text[:5]))

        query = AudioQuery(
            accent_phrases=trunc,
            speedScale=body.speed,
            pitchScale=0,
            intonationScale=1,
            volumeScale=1.2,
            prePhonemeLength=0.15,
            postPhonemeLength=0.1,
            outputSamplingRate=default_sampling_rate,
            outputStereo=False,
            kana=create_kana(accent_phrases),
        )
        wave = engine.synthesis(query=query, speaker_id=speaker, enable_interrogative_upspeak=enable_interrogative_upspeak)

        with NamedTemporaryFile(delete=False) as f:
            soundfile.write(
                file=f, data=wave, samplerate=query.outputSamplingRate, format="WAV"
            )

        return FileResponse(f.name, media_type="audio/wav")

    @app.get("/version", tags=["その他"])
    def version() -> str:
        return __version__

    @app.get("/speakers", response_model=List[Speaker], tags=["その他"])
    def speakers(
        core_version: Optional[str] = None,
    ):
        engine = get_engine(core_version)
        return Response(
            content=engine.speakers,
            media_type="application/json",
        )

    return app


if __name__ == "__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "50021")))
    parser.add_argument("--use_gpu", action="store_true")
    parser.add_argument("--voicevox_dir", type=Path, default=None)
    parser.add_argument("--voicelib_dir", type=Path, default=None, action="append")
    parser.add_argument("--runtime_dir", type=Path, default=None, action="append")
    parser.add_argument("--enable_mock", action="store_true")
    parser.add_argument("--enable_cancellable_synthesis", action="store_true")
    parser.add_argument("--init_processes", type=int, default=2)
    parser.add_argument(
        "--cpu_num_threads", type=int, default=os.getenv("VV_CPU_NUM_THREADS") or None
    )
    args = parser.parse_args()

    cpu_num_threads: Optional[int] = args.cpu_num_threads

    synthesis_engines = make_synthesis_engines(
        use_gpu=args.use_gpu,
        voicelib_dirs=args.voicelib_dir,
        voicevox_dir=args.voicevox_dir,
        runtime_dirs=args.runtime_dir,
        cpu_num_threads=cpu_num_threads,
        enable_mock=args.enable_mock,
    )
    assert len(synthesis_engines) != 0, "音声合成エンジンがありません。"
    latest_core_version = str(max([LooseVersion(ver) for ver in synthesis_engines]))

    cancellable_engine = None
    if args.enable_cancellable_synthesis:
        cancellable_engine = CancellableEngine(args)

    uvicorn.run(
        generate_app(synthesis_engines, latest_core_version),
        host=args.host,
        port=args.port,
    )
