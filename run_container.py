import argparse
import base64
import io
import os
import sys
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile
from typing import List, Optional

import numpy as np
import soundfile
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import FileResponse

from voicevox_engine.full_context_label import extract_full_context_label
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
from voicevox_engine.synthesis_engine import SynthesisEngine

kMoraLimit = 100
class TTSRequest(BaseModel):
    text: str
    speaker: int
    speed: float = 1.0

def make_synthesis_engine(
    use_gpu: bool,
    voicevox_dir: Optional[Path] = None,
    voicelib_dir: Optional[Path] = None,
) -> SynthesisEngine:
    """
    音声ライブラリをロードして、音声合成エンジンを生成

    Parameters
    ----------
    use_gpu: bool
        音声ライブラリに GPU を使わせるか否か
    voicevox_dir: Path, optional, default=None
        音声ライブラリの Python モジュールがあるディレクトリ
        None のとき、Python 標準のモジュール検索パスのどれかにあるとする
    voicelib_dir: Path, optional, default=None
        音声ライブラリ自体があるディレクトリ
        None のとき、音声ライブラリの Python モジュールと同じディレクトリにあるとする
    """

    # Python モジュール検索パスへ追加
    if voicevox_dir is not None:
        print("Notice: --voicevox_dir is " + voicevox_dir.as_posix(), file=sys.stderr)
        if voicevox_dir.exists():
            sys.path.insert(0, str(voicevox_dir))

    has_voicevox_core = True
    try:
        import core
    except ImportError:
        import traceback

        from voicevox_engine.dev import core

        has_voicevox_core = False

        # 音声ライブラリの Python モジュールをロードできなかった
        traceback.print_exc()
        print(
            "Notice: mock-library will be used. Try re-run with valid --voicevox_dir",  # noqa
            file=sys.stderr,
        )

    if voicelib_dir is None:
        if voicevox_dir is not None:
            voicelib_dir = voicevox_dir
        else:
            voicelib_dir = Path(__file__).parent  # core.__file__だとnuitkaビルド後にエラー

    core.initialize(voicelib_dir.as_posix() + "/", use_gpu)

    if has_voicevox_core:
        return SynthesisEngine(
            yukarin_s_forwarder=core.yukarin_s_forward,
            yukarin_sa_forwarder=core.yukarin_sa_forward,
            decode_forwarder=core.decode_forward,
            speakers=core.metas(),
        )

    from voicevox_engine.dev.synthesis_engine import (
        SynthesisEngine as mock_synthesis_engine,
    )

    # モックで置き換える
    return mock_synthesis_engine(speakers=core.metas())


def mora_to_text(mora: str):
    if mora[-1:] in ["A", "I", "U", "E", "O"]:
        # 無声化母音を小文字に
        mora = mora[:-1] + mora[-1].lower()
    if mora in openjtalk_mora2text:
        return openjtalk_mora2text[mora]
    else:
        return mora


def generate_app(engine: SynthesisEngine) -> FastAPI:
    root_dir = Path(__file__).parent

    default_sampling_rate = engine.default_sampling_rate

    app = FastAPI(
        title="VOICEVOX ENGINE",
        description="VOICEVOXの音声合成エンジンです。",
        version=(root_dir / "VERSION.txt").read_text().strip(),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def replace_mora_data(
        accent_phrases: List[AccentPhrase], speaker_id: int
    ) -> List[AccentPhrase]:
        return engine.replace_mora_pitch(
            accent_phrases=engine.replace_phoneme_length(
                accent_phrases=accent_phrases,
                speaker_id=speaker_id,
            ),
            speaker_id=speaker_id,
        )

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
        accent_phrases = create_accent_phrases(text, speaker_id=speaker)
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
        wave = engine.synthesis(query=query, speaker_id=speaker)

        with NamedTemporaryFile(delete=False) as f:
            soundfile.write(
                file=f, data=wave, samplerate=query.outputSamplingRate, format="WAV"
            )

        return FileResponse(f.name, media_type="audio/wav")

    @app.get("/version", tags=["その他"])
    def version() -> str:
        return (root_dir / "VERSION.txt").read_text()

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--use_gpu", action="store_true")
    parser.add_argument("--voicevox_dir", type=Path, default=None)
    parser.add_argument("--voicelib_dir", type=Path, default=None)
    args = parser.parse_args()
    if args.port is None:
        args.port = int(os.environ.get("PORT", "50021"))
    uvicorn.run(
        generate_app(
            make_synthesis_engine(
                use_gpu=args.use_gpu,
                voicevox_dir=args.voicevox_dir,
                voicelib_dir=args.voicelib_dir,
            )
        ),
        host=args.host,
        port=args.port,
    )
