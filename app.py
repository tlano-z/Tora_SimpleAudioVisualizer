from __future__ import annotations

import base64
import html
import io
import json
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

import streamlit as st
from PIL import Image

from renderer import (
    FFmpegNotFoundError,
    FONTS_DIR,
    OUTPUT_DIR,
    TMP_DIR,
    RenderError,
    RenderSettings,
    check_ffmpeg,
    ensure_project_dirs,
    make_output_path,
    render_equalizer_video,
    render_static_preview,
    timestamp_string,
)


APP_NAME = "Tora_SimpleAudioVisualizer"

PRESETS = {
    "YouTube 横長 1280x720": (1280, 720),
    "YouTube Full HD 1920x1080": (1920, 1080),
    "Shorts / TikTok 1080x1920": (1080, 1920),
    "カスタム": None,
}

EQUALIZER_MODE_OPTIONS = {
    "standard": "通常（低音から時計回り）",
    "per_band": "各周波数帯ごとに正規化",
    "distributed": "円周上に分散配置",
    "mirrored": "左右対称配置",
}

DEFAULTS = {
    "size_preset": "YouTube 横長 1280x720",
    "custom_width": 1280,
    "custom_height": 720,
    "fps": 30,
    "bars": 128,
    "equalizer_mode": "standard",
    "radius": 210,
    "bar_length": 100,
    "center_size": 320,
    "center_crop_x": 50,
    "center_crop_y": 50,
    "center_crop_size": 100,
    "background_blur": 10,
    "background_darkness": 0.1,
    "smoothing": 0.75,
    "fft_size": 4096,
    "bar_color": "#FFFFFF",
    "glow_color": "#50B4FF",
    "font_color": "#FFFFFF",
    "font_file": "",
    "ring_enabled": True,
    "pulse_enabled": True,
    "preview_start": 0.0,
    "preview_duration": 5.0,
    "preview_lightweight": True,
    "title_text": "",
    "artist_text": "",
    "caption_text": "",
}

VISUAL_DEFAULT_KEYS = [
    "bars",
    "equalizer_mode",
    "radius",
    "bar_length",
    "center_size",
    "background_blur",
    "background_darkness",
    "smoothing",
    "bar_color",
    "glow_color",
    "ring_enabled",
    "pulse_enabled",
]


def init_state() -> None:
    for key, value in DEFAULTS.items():
        st.session_state.setdefault(key, value)
    st.session_state.setdefault("last_preview_data", b"")
    st.session_state.setdefault("last_preview_name", "")
    st.session_state.setdefault("last_output_path", "")
    st.session_state.setdefault("last_settings_path", "")


def reset_visual_settings() -> None:
    for key in VISUAL_DEFAULT_KEYS:
        st.session_state[key] = DEFAULTS[key]


def save_upload(uploaded_file, prefix: str) -> Path:
    suffix = Path(uploaded_file.name).suffix.lower()
    name = f"{prefix}_{timestamp_string()}_{uuid.uuid4().hex[:8]}{suffix}"
    path = TMP_DIR / name
    path.write_bytes(uploaded_file.getbuffer())
    return path


def remove_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def current_dimensions() -> tuple[int, int]:
    preset = st.session_state["size_preset"]
    if PRESETS[preset] is not None:
        return PRESETS[preset]
    width = int(st.session_state["custom_width"])
    height = int(st.session_state["custom_height"])
    return width, height


def build_settings(width: int, height: int) -> RenderSettings:
    equalizer_mode = str(st.session_state.get("equalizer_mode", DEFAULTS["equalizer_mode"]))
    if equalizer_mode not in EQUALIZER_MODE_OPTIONS:
        equalizer_mode = DEFAULTS["equalizer_mode"]

    return RenderSettings(
        width=width,
        height=height,
        fps=int(st.session_state["fps"]),
        bars=int(st.session_state["bars"]),
        equalizer_mode=equalizer_mode,
        radius=int(st.session_state["radius"]),
        bar_length=int(st.session_state["bar_length"]),
        center_size=int(st.session_state["center_size"]),
        center_crop_x=float(st.session_state["center_crop_x"]) / 100.0,
        center_crop_y=float(st.session_state["center_crop_y"]) / 100.0,
        center_crop_size=float(st.session_state["center_crop_size"]) / 100.0,
        background_blur=int(st.session_state["background_blur"]),
        background_darkness=float(st.session_state["background_darkness"]),
        smoothing=float(st.session_state["smoothing"]),
        fft_size=int(st.session_state["fft_size"]),
        bar_color=str(st.session_state["bar_color"]),
        glow_color=str(st.session_state["glow_color"]),
        font_color=str(st.session_state["font_color"]),
        font_file=str(st.session_state["font_file"]),
        ring_enabled=bool(st.session_state["ring_enabled"]),
        pulse_enabled=bool(st.session_state["pulse_enabled"]),
        title_text=str(st.session_state["title_text"]),
        artist_text=str(st.session_state["artist_text"]),
        caption_text=str(st.session_state["caption_text"]),
    )


def scaled_preview_settings(settings: RenderSettings) -> RenderSettings:
    if not st.session_state["preview_lightweight"]:
        return settings
    scale = min(1.0, 640.0 / float(settings.width))
    if scale >= 1.0:
        return settings
    width = max(2, int(round(settings.width * scale / 2.0)) * 2)
    height = max(2, int(round(settings.height * scale / 2.0)) * 2)
    return replace(
        settings,
        width=width,
        height=height,
        radius=max(12, int(settings.radius * scale)),
        bar_length=max(8, int(settings.bar_length * scale)),
        center_size=max(32, int(settings.center_size * scale)),
        background_blur=max(0, int(settings.background_blur * scale)),
    )


def static_preview_settings(settings: RenderSettings) -> RenderSettings:
    max_side = max(settings.width, settings.height)
    scale = min(1.0, 960.0 / float(max_side))
    if scale >= 1.0:
        return settings
    width = max(2, int(round(settings.width * scale / 2.0)) * 2)
    height = max(2, int(round(settings.height * scale / 2.0)) * 2)
    return replace(
        settings,
        width=width,
        height=height,
        radius=max(12, int(settings.radius * scale)),
        bar_length=max(8, int(settings.bar_length * scale)),
        center_size=max(32, int(settings.center_size * scale)),
        background_blur=max(0, int(settings.background_blur * scale)),
    )


def font_options() -> dict[str, str]:
    FONTS_DIR.mkdir(exist_ok=True)
    options = {"デフォルト": ""}
    font_files = sorted(
        [
            path
            for path in FONTS_DIR.rglob("*")
            if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}
        ],
        key=lambda path: path.name.lower(),
    )
    for path in font_files:
        label = path.relative_to(FONTS_DIR).as_posix()
        options[label] = label
    return options


def apply_compact_css() -> None:
    st.markdown(
        """
        <style>
        header[data-testid="stHeader"],
        div[data-testid="stToolbar"],
        div[data-testid="stDecoration"],
        #MainMenu {
            display: none !important;
        }
        .block-container { padding: 0.75rem 1rem 1rem; max-width: 100%; }
        h1 { font-size: 1.45rem !important; margin: 0 0 0.25rem !important; }
        h2, h3 { font-size: 1.02rem !important; margin: 0.35rem 0 0.2rem !important; }
        p, label, span, div[data-testid="stMarkdownContainer"] { font-size: 0.84rem; }
        div[data-testid="stVerticalBlock"] { gap: 0.35rem; }
        div[data-testid="stHorizontalBlock"] { gap: 0.55rem; }
        div[data-testid="stFileUploader"] { padding-bottom: 0.05rem; }
        div[data-testid="stFileUploader"] section { padding: 0.3rem; min-height: 2.45rem; }
        div[data-baseweb="select"] > div { min-height: 2rem; }
        div[data-testid="stSlider"] { padding-top: 0.05rem; padding-bottom: 0.05rem; }
        div[data-testid="stNumberInput"] input, div[data-testid="stTextInput"] input {
            min-height: 1.95rem;
            padding-top: 0.15rem;
            padding-bottom: 0.15rem;
        }
        button[kind="primary"], button[kind="secondary"] { min-height: 2.05rem; padding: 0.15rem 0.45rem; }
        .stAlert { padding: 0.35rem 0.55rem; }
        div[data-testid="stTabs"] button { padding: 0.28rem 0.38rem; }
        div[data-testid="stTabs"] button p { font-size: 0.78rem; }
        div[data-testid="stTabs"] [role="tablist"] { gap: 0.2rem; }
        .preview-media-frame {
            width: 100%;
            height: clamp(300px, calc(100vh - 210px), 620px);
            border: 1px solid rgba(120, 140, 180, 0.28);
            border-radius: 8px;
            background: #05070c;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }
        .preview-media-frame img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }
        .preview-media-frame .placeholder {
            color: #7db7ff;
            padding: 1rem;
            text-align: center;
        }
        .crop-selector-frame {
            position: relative;
            width: 100%;
            max-width: 310px;
            margin-top: 0.2rem;
            border: 1px solid rgba(120, 140, 180, 0.30);
            border-radius: 8px;
            background: #05070c;
            overflow: hidden;
        }
        .crop-selector-frame img {
            width: 100%;
            height: auto;
            display: block;
        }
        .crop-selector-box {
            position: absolute;
            border: 2px solid #ffffff;
            box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.42), 0 0 16px rgba(80, 180, 255, 0.75);
            outline: 1px solid rgba(80, 180, 255, 0.95);
            pointer-events: none;
        }
        div[data-testid="stVideo"] video {
            width: 100% !important;
            height: clamp(300px, calc(100vh - 245px), 600px) !important;
            object-fit: contain !important;
            background: #05070c;
            border: 1px solid rgba(120, 140, 180, 0.28);
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def apply_loaded_settings(data: dict) -> None:
    for key in DEFAULTS:
        if key in data:
            value = data[key]
            if key in {"center_crop_x", "center_crop_y", "center_crop_size"} and isinstance(value, (int, float)):
                value = value * 100.0 if value <= 1.0 else value
            st.session_state[key] = value

    if "width" in data and "height" in data and data.get("size_preset") not in PRESETS:
        st.session_state["size_preset"] = "カスタム"
        st.session_state["custom_width"] = int(data["width"])
        st.session_state["custom_height"] = int(data["height"])


def save_settings_json(settings: RenderSettings) -> Path:
    data = asdict(settings)
    data.update(
        {
            "size_preset": st.session_state["size_preset"],
            "custom_width": st.session_state["custom_width"],
            "custom_height": st.session_state["custom_height"],
            "preview_start": st.session_state["preview_start"],
            "preview_duration": st.session_state["preview_duration"],
            "preview_lightweight": st.session_state["preview_lightweight"],
            "center_crop_x": st.session_state["center_crop_x"],
            "center_crop_y": st.session_state["center_crop_y"],
            "center_crop_size": st.session_state["center_crop_size"],
        }
    )
    path = OUTPUT_DIR / f"settings_{timestamp_string()}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def image_data_url(frame) -> str:
    buffer = io.BytesIO()
    Image.fromarray(frame).save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def show_media_placeholder(message: str) -> None:
    escaped = html.escape(message)
    st.markdown(
        f'<div class="preview-media-frame"><div class="placeholder">{escaped}</div></div>',
        unsafe_allow_html=True,
    )


def show_image_frame(frame, alt: str) -> None:
    data_url = image_data_url(frame)
    escaped_alt = html.escape(alt)
    st.markdown(
        f'<div class="preview-media-frame"><img src="{data_url}" alt="{escaped_alt}"></div>',
        unsafe_allow_html=True,
    )


def uploaded_image_data_url(image_file) -> str:
    suffix = Path(image_file.name).suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
    encoded = base64.b64encode(image_file.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def uploaded_image_size(image_file) -> tuple[int, int] | None:
    if image_file is None:
        return None
    try:
        with Image.open(io.BytesIO(image_file.getvalue())) as image:
            return image.size
    except Exception:
        return None


def show_crop_selector(image_file) -> None:
    if image_file is None:
        st.info("画像を選択すると切り抜き位置を確認できます。")
        return

    image_size = uploaded_image_size(image_file)
    if image_size is None:
        st.warning("切り抜き位置の表示に画像サイズを取得できませんでした。")
        return

    image_width, image_height = image_size
    crop_size = min(image_width, image_height) * float(st.session_state["center_crop_size"]) / 100.0
    crop_size = max(1.0, min(crop_size, image_width, image_height))
    left = float(st.session_state["center_crop_x"]) / 100.0 * image_width - crop_size / 2.0
    top = float(st.session_state["center_crop_y"]) / 100.0 * image_height - crop_size / 2.0
    left = min(max(left, 0.0), image_width - crop_size)
    top = min(max(top, 0.0), image_height - crop_size)

    left_pct = left / image_width * 100.0
    top_pct = top / image_height * 100.0
    width_pct = crop_size / image_width * 100.0
    height_pct = crop_size / image_height * 100.0
    data_url = uploaded_image_data_url(image_file)
    st.markdown(
        f"""
        <div class="crop-selector-frame">
          <img src="{data_url}" alt="中央画像切り抜き指定">
          <div class="crop-selector-box"
               style="left:{left_pct:.4f}%; top:{top_pct:.4f}%; width:{width_pct:.4f}%; height:{height_pct:.4f}%;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_action(
    image_file,
    audio_file,
    settings: RenderSettings,
    *,
    preview: bool,
) -> None:
    if image_file is None or audio_file is None:
        st.error("画像ファイルと音声ファイルの両方を選択してください。")
        return

    tmp_paths: list[Path] = []
    try:
        image_path = save_upload(image_file, "image")
        audio_path = save_upload(audio_file, "audio")
        tmp_paths.extend([image_path, audio_path])

        if preview:
            output_path = TMP_DIR / f"preview_{timestamp_string()}_{uuid.uuid4().hex[:8]}.mp4"
            tmp_paths.append(output_path)
        else:
            output_path = make_output_path("equalizer_video")
        start_time = float(st.session_state["preview_start"]) if preview else 0.0
        duration = float(st.session_state["preview_duration"]) if preview else None
        render_settings = scaled_preview_settings(settings) if preview else settings

        progress = st.progress(0.0, text="準備中です")
        last_update = {"time": 0.0}

        def on_progress(value: float, message: str) -> None:
            now = time.monotonic()
            if value >= 1.0 or now - last_update["time"] > 0.15:
                progress.progress(min(max(value, 0.0), 1.0), text=message)
                last_update["time"] = now

        result = render_equalizer_video(
            image_path=image_path,
            audio_path=audio_path,
            settings=render_settings,
            output_path=output_path,
            start_time=start_time,
            duration=duration,
            progress_callback=on_progress,
        )
        progress.progress(1.0, text="完了しました")

        if preview:
            st.session_state["last_preview_data"] = result.read_bytes()
            st.session_state["last_preview_name"] = result.name
            st.success(f"プレビューを生成しました: {result.name}")
        else:
            st.session_state["last_output_path"] = str(result)
            st.success(f"本番動画を生成しました: {result.name}")
    except FFmpegNotFoundError as exc:
        st.error(str(exc))
    except RenderError as exc:
        st.error(str(exc))
    except Exception as exc:  # Streamlit上で原因を表示し、アプリ全体の停止を避ける。
        st.error(f"予期しないエラーが発生しました: {exc}")
    finally:
        remove_paths(tmp_paths)


def show_preview_result(data: bytes, file_name: str) -> None:
    if not data:
        show_media_placeholder("動画を生成するとここに表示されます。")
        return

    st.video(data)
    st.download_button(
        "プレビューMP4をダウンロード",
        data=data,
        file_name=file_name or "preview.mp4",
        mime="video/mp4",
        use_container_width=True,
    )


def show_video_result(path_value: str, download_label: str) -> None:
    if not path_value:
        show_media_placeholder("動画を生成するとここに表示されます。")
        return
    path = Path(path_value)
    if not path.exists():
        show_media_placeholder(f"動画ファイルが見つかりません: {path.name}")
        return

    st.video(str(path))
    with path.open("rb") as file:
        st.download_button(
            download_label,
            data=file,
            file_name=path.name,
            mime="video/mp4",
            use_container_width=True,
        )


def show_static_visual_preview(image_file, settings: RenderSettings) -> None:
    if image_file is None:
        show_media_placeholder("画像を選択すると、設定を反映した静止プレビューが表示されます。")
        return

    tmp_paths: list[Path] = []
    try:
        image_path = save_upload(image_file, "static_preview_image")
        tmp_paths.append(image_path)
        preview_frame = render_static_preview(image_path, static_preview_settings(settings))
        show_image_frame(preview_frame, "静止プレビュー")
    except RenderError as exc:
        show_media_placeholder(str(exc))
    except Exception as exc:
        show_media_placeholder(f"静止プレビューを生成できませんでした: {exc}")
    finally:
        remove_paths(tmp_paths)


def main() -> None:
    st.set_page_config(page_title=APP_NAME, layout="wide")
    apply_compact_css()

    ensure_project_dirs()
    init_state()

    title_col, status_col = st.columns([0.58, 0.42], gap="medium")
    with title_col:
        st.title(APP_NAME)

    ffmpeg_ok, ffmpeg_message = check_ffmpeg()
    with status_col:
        if ffmpeg_ok:
            st.caption("FFmpeg / ffprobe: OK")
        else:
            st.warning(ffmpeg_message)

    main_left, main_right = st.columns([0.32, 0.68], gap="medium")

    with main_left:
        st.subheader("入力")
        input_a, input_b = st.columns([0.50, 0.50], gap="small")
        with input_a:
            image_file = st.file_uploader("サムネイル画像", type=["jpg", "jpeg", "png", "webp"])
            if image_file is not None:
                st.image(image_file, caption=image_file.name, width=135)
        with input_b:
            audio_file = st.file_uploader("音声ファイル", type=["mp3", "wav", "m4a", "aac"])
            if audio_file is not None:
                st.audio(audio_file)

        tab_video, tab_visual, tab_crop, tab_text, tab_preview, tab_full, tab_json = st.tabs(
            ["動画", "見た目", "切抜き", "文字", "プレビュー", "本番", "JSON"]
        )

        with tab_video:
            video_a, video_b = st.columns(2, gap="small")
            with video_a:
                st.selectbox("サイズ", list(PRESETS.keys()), key="size_preset")
                if st.session_state["size_preset"] == "カスタム":
                    size_a, size_b = st.columns(2, gap="small")
                    with size_a:
                        st.number_input("幅", min_value=320, max_value=3840, step=2, key="custom_width")
                    with size_b:
                        st.number_input("高さ", min_value=320, max_value=3840, step=2, key="custom_height")
            with video_b:
                st.selectbox("FPS", [24, 30, 60], key="fps")
                st.selectbox("FFT", [1024, 2048, 4096, 8192], key="fft_size")

        width, height = current_dimensions()

        with tab_visual:
            if st.button("見た目を初期値に戻す", use_container_width=True):
                reset_visual_settings()
                st.rerun()

            if st.session_state["equalizer_mode"] not in EQUALIZER_MODE_OPTIONS:
                st.session_state["equalizer_mode"] = DEFAULTS["equalizer_mode"]
            st.selectbox(
                "ビジュアライザーモード",
                list(EQUALIZER_MODE_OPTIONS.keys()),
                key="equalizer_mode",
                format_func=lambda key: EQUALIZER_MODE_OPTIONS[key],
            )

            visual_a, visual_b, visual_c = st.columns(3, gap="small")
            with visual_a:
                st.slider("バー本数", 16, 256, step=8, key="bars")
                st.slider("円半径", 40, 900, step=5, key="radius")
            with visual_b:
                st.slider("バー長", 10, 600, step=5, key="bar_length")
                st.slider("中央画像", 40, 900, step=5, key="center_size")
            with visual_c:
                st.slider("背景ぼかし", 0, 50, step=1, key="background_blur")
                st.slider("背景暗さ", 0.0, 0.9, step=0.01, key="background_darkness")
                st.slider("滑らかさ", 0.0, 0.95, step=0.01, key="smoothing")
            color_a, color_b, color_c, color_d = st.columns(4, gap="small")
            with color_a:
                st.color_picker("バー色", key="bar_color")
            with color_b:
                st.color_picker("グロー", key="glow_color")
            with color_c:
                st.checkbox("リング", key="ring_enabled")
            with color_d:
                st.checkbox("脈動", key="pulse_enabled")

        with tab_crop:
            crop_a, crop_b = st.columns([0.45, 0.55], gap="small")
            with crop_a:
                st.slider("切抜きX", 0, 100, step=1, key="center_crop_x")
                st.slider("切抜きY", 0, 100, step=1, key="center_crop_y")
                st.slider("切抜きサイズ", 10, 100, step=1, key="center_crop_size")
            with crop_b:
                show_crop_selector(image_file)

        with tab_text:
            text_a, text_b = st.columns(2, gap="small")
            with text_a:
                st.text_input("曲名", key="title_text")
                st.text_input("アーティスト", key="artist_text")
                st.text_input("キャプション", key="caption_text")
            with text_b:
                font_map = font_options()
                if st.session_state["font_file"] not in font_map.values():
                    st.session_state["font_file"] = ""
                labels = list(font_map.keys())
                current_label = next(label for label, value in font_map.items() if value == st.session_state["font_file"])
                selected_label = st.selectbox("フォント", labels, index=labels.index(current_label))
                st.session_state["font_file"] = font_map[selected_label]
                st.color_picker("文字色", key="font_color")

        with tab_preview:
            export_a, export_b, export_c = st.columns(3, gap="small")
            with export_a:
                st.number_input("開始秒", min_value=0.0, step=1.0, key="preview_start")
            with export_b:
                st.number_input("秒数", min_value=1.0, max_value=60.0, step=1.0, key="preview_duration")
            with export_c:
                st.checkbox("軽量", key="preview_lightweight")

            if st.button("プレビュー動画を生成", type="primary", use_container_width=True):
                settings = build_settings(width, height)
                render_action(image_file, audio_file, settings, preview=True)

        with tab_full:
            st.caption("本番生成は音声全体の長さで書き出します。開始秒や秒数指定は使いません。")
            if st.button("本番動画を生成", type="primary", use_container_width=True):
                settings = build_settings(width, height)
                render_action(image_file, audio_file, settings, preview=False)

        with tab_json:
            settings_file = st.file_uploader("設定JSON", type=["json"])
            action_c, action_d = st.columns(2, gap="small")
            with action_c:
                if st.button("設定保存", use_container_width=True):
                    settings = build_settings(width, height)
                    path = save_settings_json(settings)
                    st.session_state["last_settings_path"] = str(path)
                    st.success(f"保存: {path.name}")
            with action_d:
                if st.button("設定反映", use_container_width=True):
                    if settings_file is None:
                        st.warning("JSONを選択してください。")
                    else:
                        try:
                            data = json.loads(settings_file.getvalue().decode("utf-8"))
                            apply_loaded_settings(data)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"設定JSONを読み込めませんでした: {exc}")

    settings = build_settings(width, height)

    with main_right:
        st.subheader("表示")
        view_static, view_preview, view_full = st.tabs(["静止プレビュー", "プレビュー動画", "本番動画"])
        with view_static:
            show_static_visual_preview(image_file, settings)
        with view_preview:
            show_preview_result(
                st.session_state["last_preview_data"],
                st.session_state["last_preview_name"],
            )
        with view_full:
            show_video_result(
                st.session_state["last_output_path"],
                "完成MP4をダウンロード",
            )

        if st.session_state["last_settings_path"]:
            settings_path = Path(st.session_state["last_settings_path"])
            if settings_path.exists():
                with settings_path.open("rb") as file:
                    st.download_button(
                        "保存した設定JSONをダウンロード",
                        data=file,
                        file_name=settings_path.name,
                        mime="application/json",
                        use_container_width=True,
                    )


if __name__ == "__main__":
    main()
