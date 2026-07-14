from __future__ import annotations

import math
import shutil
import subprocess
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
TMP_DIR = PROJECT_ROOT / "tmp"
FONTS_DIR = PROJECT_ROOT / "fonts"
LOCAL_FFMPEG_BIN = PROJECT_ROOT / "tools" / "ffmpeg" / "bin"

FFMPEG_MISSING_MESSAGE = (
    "FFmpegまたはffprobeが見つかりません。\n"
    "このアプリを使うにはFFmpegのインストールが必要です。\n"
    "インストール後、ffmpegコマンドが使える状態で再度実行してください。"
)

ProgressCallback = Callable[[float, str], None]


class FFmpegNotFoundError(RuntimeError):
    pass


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderSettings:
    width: int = 1280
    height: int = 720
    fps: int = 30
    bars: int = 128
    equalizer_mode: str = "standard"
    radius: int = 210
    bar_length: int = 130
    center_size: int = 300
    center_crop_x: float = 0.5
    center_crop_y: float = 0.5
    center_crop_size: float = 1.0
    background_blur: int = 18
    background_darkness: float = 0.55
    smoothing: float = 0.75
    fft_size: int = 4096
    bar_color: str = "#FFFFFF"
    glow_color: str = "#50B4FF"
    ring_enabled: bool = True
    pulse_enabled: bool = True
    title_text: str = ""
    artist_text: str = ""
    caption_text: str = ""
    font_color: str = "#FFFFFF"
    font_file: str = ""


def ensure_project_dirs() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)
    FONTS_DIR.mkdir(exist_ok=True)


def timestamp_string() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def make_output_path(prefix: str) -> Path:
    ensure_project_dirs()
    return OUTPUT_DIR / f"{prefix}_{timestamp_string()}.mp4"


def check_ffmpeg() -> tuple[bool, str]:
    missing = [name for name in ("ffmpeg", "ffprobe") if _command_path(name) is None]
    if missing:
        return False, f"{FFMPEG_MISSING_MESSAGE}\n見つからないコマンド: {', '.join(missing)}"
    return True, "FFmpegとffprobeを検出しました。"


def require_ffmpeg() -> None:
    ok, message = check_ffmpeg()
    if not ok:
        raise FFmpegNotFoundError(message)


def _command_path(name: str) -> str | None:
    executable = f"{name}.exe" if not name.lower().endswith(".exe") else name
    local_path = LOCAL_FFMPEG_BIN / executable
    if local_path.exists():
        return str(local_path)
    return shutil.which(name)


def _ffmpeg() -> str:
    path = _command_path("ffmpeg")
    if path is None:
        raise FFmpegNotFoundError(FFMPEG_MISSING_MESSAGE)
    return path


def _ffprobe() -> str:
    path = _command_path("ffprobe")
    if path is None:
        raise FFmpegNotFoundError(FFMPEG_MISSING_MESSAGE)
    return path


def _run_command(command: list[str], error_prefix: str) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        stderr = process.stderr.strip() or process.stdout.strip() or "詳細なエラー出力はありません。"
        raise RenderError(f"{error_prefix}\n{stderr}")
    return process


def _format_seconds(value: float) -> str:
    return f"{max(0.0, float(value)):.3f}"


def get_audio_duration(audio_path: Path | str) -> float:
    require_ffmpeg()
    command = [
        _ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    process = _run_command(command, "音声の長さを取得できませんでした。")
    raw_value = process.stdout.strip()
    try:
        duration = float(raw_value)
    except ValueError as exc:
        raise RenderError(f"音声の長さを数値として読み取れませんでした: {raw_value}") from exc
    if duration <= 0:
        raise RenderError(f"音声の長さが不正です: {duration}")
    return duration


def _convert_audio_to_wav(
    audio_path: Path,
    wav_path: Path,
    *,
    start_time: float,
    duration: float,
) -> None:
    command = [_ffmpeg(), "-y"]
    if start_time > 0:
        command.extend(["-ss", _format_seconds(start_time)])
    command.extend(["-i", str(audio_path)])
    command.extend(
        [
            "-t",
            _format_seconds(duration),
            "-ac",
            "1",
            "-ar",
            "44100",
            "-vn",
            "-acodec",
            "pcm_s16le",
            str(wav_path),
        ]
    )
    _run_command(command, "音声を解析用WAVへ変換できませんでした。")


def _read_wav_mono(wav_path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(wav_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise RenderError(f"未対応のWAVサンプル幅です: {sample_width} bytes")

    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)

    return samples, sample_rate


def _band_indices(sample_rate: int, fft_size: int, bars: int) -> list[np.ndarray]:
    frequencies = np.fft.rfftfreq(fft_size, d=1.0 / float(sample_rate))
    min_freq = 40.0
    max_freq = min(12000.0, sample_rate / 2.0)
    edges = np.geomspace(min_freq, max_freq, bars + 1)
    result: list[np.ndarray] = []
    for low, high in zip(edges[:-1], edges[1:]):
        indices = np.where((frequencies >= low) & (frequencies < high))[0]
        if indices.size == 0:
            center = (low + high) * 0.5
            indices = np.array([int(np.argmin(np.abs(frequencies - center)))])
        result.append(indices)
    return result


def _analyze_spectra(
    samples: np.ndarray,
    sample_rate: int,
    settings: RenderSettings,
    duration: float,
) -> tuple[np.ndarray, np.ndarray]:
    frame_count = max(1, int(math.ceil(duration * settings.fps)))
    fft_size = int(settings.fft_size)
    half_window = fft_size // 2
    window = np.hanning(fft_size).astype(np.float32)
    bands = _band_indices(sample_rate, fft_size, settings.bars)
    padded = np.pad(samples.astype(np.float32), (half_window, half_window), mode="constant")

    raw = np.zeros((frame_count, settings.bars), dtype=np.float32)
    volumes = np.zeros(frame_count, dtype=np.float32)

    for frame_index in range(frame_count):
        time_position = frame_index / float(settings.fps)
        center = int(time_position * sample_rate) + half_window
        chunk = padded[center - half_window : center + half_window]
        if chunk.size < fft_size:
            chunk = np.pad(chunk, (0, fft_size - chunk.size), mode="constant")

        volumes[frame_index] = float(np.sqrt(np.mean(chunk * chunk)))
        magnitudes = np.abs(np.fft.rfft(chunk * window))
        for band_index, indices in enumerate(bands):
            raw[frame_index, band_index] = float(np.mean(magnitudes[indices]))

    raw = np.log1p(raw)
    if settings.equalizer_mode == "per_band":
        global_norm = float(np.percentile(raw, 98)) if raw.size else 1.0
        if global_norm <= 1e-6:
            global_norm = 1.0
        band_norms = np.percentile(raw, 98, axis=0) if raw.size else np.ones(settings.bars)
        band_norms = np.maximum(band_norms, global_norm * 0.06)
        raw = np.clip(raw / band_norms[np.newaxis, :], 0.0, 1.0)
    else:
        spectrum_norm = float(np.percentile(raw, 98)) if raw.size else 1.0
        if spectrum_norm <= 1e-6:
            spectrum_norm = 1.0
        raw = np.clip(raw / spectrum_norm, 0.0, 1.0)

    smoothing = min(max(float(settings.smoothing), 0.0), 0.98)
    smoothed = np.zeros_like(raw)
    current = np.zeros(settings.bars, dtype=np.float32)
    for frame_index in range(frame_count):
        current = current * smoothing + raw[frame_index] * (1.0 - smoothing)
        smoothed[frame_index] = current

    volumes = np.log1p(volumes * 12.0)
    volume_norm = float(np.percentile(volumes, 95)) if volumes.size else 1.0
    if volume_norm <= 1e-6:
        volume_norm = 1.0
    volumes = np.clip(volumes / volume_norm, 0.0, 1.0)

    return smoothed, volumes


def _resample_filter() -> int:
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _load_image(image_path: Path) -> Image.Image:
    try:
        return Image.open(image_path).convert("RGB")
    except Exception as exc:
        raise RenderError(f"画像を読み込めませんでした: {exc}") from exc


def _make_background(image: Image.Image, settings: RenderSettings) -> np.ndarray:
    background = ImageOps.fit(
        image,
        (settings.width, settings.height),
        method=_resample_filter(),
        centering=(0.5, 0.5),
    )
    if settings.background_blur > 0:
        background = background.filter(ImageFilter.GaussianBlur(radius=settings.background_blur))

    darkness = min(max(float(settings.background_darkness), 0.0), 0.95)
    array = np.asarray(background).astype(np.float32) * (1.0 - darkness)
    array = np.clip(array, 0, 255).astype(np.uint8)
    return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)


def _center_crop_box(image: Image.Image, settings: RenderSettings) -> tuple[int, int, int, int]:
    width, height = image.size
    side = int(round(min(width, height) * min(max(settings.center_crop_size, 0.1), 1.0)))
    side = max(1, min(side, width, height))

    center_x = min(max(settings.center_crop_x, 0.0), 1.0) * width
    center_y = min(max(settings.center_crop_y, 0.0), 1.0) * height
    left = int(round(center_x - side / 2.0))
    top = int(round(center_y - side / 2.0))
    left = min(max(left, 0), width - side)
    top = min(max(top, 0), height - side)
    return left, top, left + side, top + side


def _center_image(image: Image.Image, size: int, settings: RenderSettings) -> np.ndarray:
    size = max(2, int(size))
    source_crop = image.crop(_center_crop_box(image, settings))
    crop = source_crop.resize((size, size), resample=_resample_filter()).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    crop.putalpha(mask)
    return np.asarray(crop)


def _overlay_rgba(frame: np.ndarray, overlay_rgba: np.ndarray, x: int, y: int) -> None:
    frame_h, frame_w = frame.shape[:2]
    overlay_h, overlay_w = overlay_rgba.shape[:2]

    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(frame_w, x + overlay_w)
    y2 = min(frame_h, y + overlay_h)
    if x1 >= x2 or y1 >= y2:
        return

    overlay_x1 = x1 - x
    overlay_y1 = y1 - y
    overlay_x2 = overlay_x1 + (x2 - x1)
    overlay_y2 = overlay_y1 + (y2 - y1)

    overlay = overlay_rgba[overlay_y1:overlay_y2, overlay_x1:overlay_x2]
    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
    overlay_bgr = overlay[:, :, :3][:, :, ::-1].astype(np.float32)
    base = frame[y1:y2, x1:x2].astype(np.float32)
    blended = overlay_bgr * alpha + base * (1.0 - alpha)
    frame[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)


def _hex_to_bgr(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return (255, 255, 255)
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except ValueError:
        return (255, 255, 255)
    return (blue, green, red)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    blue, green, red = _hex_to_bgr(value)
    return (red, green, blue)


def _draw_equalizer(frame: np.ndarray, spectrum: np.ndarray, volume: float, settings: RenderSettings) -> None:
    height, width = frame.shape[:2]
    center = (width // 2, height // 2)
    min_side = min(width, height)
    pulse = 1.0 + (float(volume) * 0.055 if settings.pulse_enabled else 0.0)
    max_radius = max(12, int(min_side * 0.5) - 8)
    radius = min(max_radius, max(8, int(settings.radius * pulse)))
    max_length = max(4, int(settings.bar_length * (1.0 + (float(volume) * 0.12 if settings.pulse_enabled else 0.0))))

    bar_color = _hex_to_bgr(settings.bar_color)
    glow_color = _hex_to_bgr(settings.glow_color)
    bar_thickness = max(2, int(round(min_side / 360.0)))
    glow_thickness = max(8, bar_thickness * 7)
    glow_layer = np.zeros_like(frame)

    display_spectrum = _display_spectrum(spectrum, settings)
    angles = np.linspace(0.0, math.tau, settings.bars, endpoint=False) - (math.pi / 2.0)

    if settings.ring_enabled:
        cv2.circle(glow_layer, center, radius, glow_color, glow_thickness, lineType=cv2.LINE_AA)

    for angle, strength in zip(angles, display_spectrum):
        length = 3 + int(float(strength) * max_length)
        unit_x = math.cos(float(angle))
        unit_y = math.sin(float(angle))
        start = (int(center[0] + unit_x * radius), int(center[1] + unit_y * radius))
        end = (int(center[0] + unit_x * (radius + length)), int(center[1] + unit_y * (radius + length)))
        cv2.line(glow_layer, start, end, glow_color, glow_thickness, lineType=cv2.LINE_AA)

    glow_sigma = max(4.0, min_side / 180.0)
    glow_layer = cv2.GaussianBlur(glow_layer, (0, 0), sigmaX=glow_sigma, sigmaY=glow_sigma)
    frame[:, :, :] = cv2.addWeighted(frame, 1.0, glow_layer, 0.46, 0.0)

    if settings.ring_enabled:
        cv2.circle(frame, center, radius, bar_color, bar_thickness, lineType=cv2.LINE_AA)

    for angle, strength in zip(angles, display_spectrum):
        length = 3 + int(float(strength) * max_length)
        unit_x = math.cos(float(angle))
        unit_y = math.sin(float(angle))
        start = (int(center[0] + unit_x * radius), int(center[1] + unit_y * radius))
        end = (int(center[0] + unit_x * (radius + length)), int(center[1] + unit_y * (radius + length)))
        cv2.line(frame, start, end, bar_color, bar_thickness, lineType=cv2.LINE_AA)


def _display_spectrum(spectrum: np.ndarray, settings: RenderSettings) -> np.ndarray:
    values = np.asarray(spectrum, dtype=np.float32)
    if values.size <= 1:
        return values

    if settings.equalizer_mode == "distributed":
        return values[_distributed_band_order(values.size)]
    if settings.equalizer_mode == "mirrored":
        return _mirrored_spectrum(values)
    return values


def _distributed_band_order(count: int) -> np.ndarray:
    chunks = np.array_split(np.arange(count), 4)
    order: list[int] = []
    max_length = max(len(chunk) for chunk in chunks)
    for offset in range(max_length):
        for chunk in chunks:
            if offset < len(chunk):
                order.append(int(chunk[offset]))
    return np.asarray(order, dtype=np.int32)


def _mirrored_spectrum(spectrum: np.ndarray) -> np.ndarray:
    count = int(spectrum.size)
    if count <= 1:
        return spectrum

    source_x = np.arange(count, dtype=np.float32)
    right_count = (count // 2) + 1
    right_x = np.linspace(0.0, float(count - 1), right_count, dtype=np.float32)
    right_values = np.interp(right_x, source_x, spectrum).astype(np.float32)
    arranged = np.zeros(count, dtype=np.float32)
    for index, value in enumerate(right_values):
        arranged[index] = value
        arranged[(-index) % count] = value
    return arranged


def _resolve_font_path(font_file: str) -> Path | None:
    if not font_file:
        return None

    candidate = (FONTS_DIR / font_file).resolve()
    fonts_root = FONTS_DIR.resolve()
    try:
        candidate.relative_to(fonts_root)
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() in {".ttf", ".otf", ".ttc"}:
        return candidate
    return None


def _load_font(font_file: str, size: int) -> ImageFont.ImageFont:
    font_path = _resolve_font_path(font_file)
    if font_path is not None:
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError:
            pass

    for bundled_name in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(bundled_name, size=size)
        except OSError:
            continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_font(line: str, font_file: str, size: int, min_size: int, max_width: int) -> ImageFont.ImageFont:
    size = max(min_size, int(size))
    while size >= min_size:
        font = _load_font(font_file, size)
        left, top, right, bottom = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), line, font=font)
        if right - left <= max_width:
            return font
        size = int(size * 0.92)
    return _load_font(font_file, min_size)


def _draw_text(frame: np.ndarray, settings: RenderSettings) -> None:
    lines = [settings.title_text.strip(), settings.artist_text.strip(), settings.caption_text.strip()]
    lines = [line for line in lines if line]
    if not lines:
        return

    height, width = frame.shape[:2]
    min_side = min(width, height)
    max_width = max(120, width - int(width * 0.09))
    base_title = max(18, int(min_side * 0.058))
    base_body = max(14, int(min_side * 0.038))
    min_size = max(10, int(min_side * 0.018))

    rendered: list[tuple[str, ImageFont.ImageFont, tuple[int, int, int, int]]] = []
    for index, line in enumerate(lines):
        size = base_title if index == 0 else base_body
        font = _fit_font(line, settings.font_file, size, min_size, max_width)
        bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), line, font=font)
        rendered.append((line, font, bbox))

    line_gap = max(4, int(min_side * 0.018))
    block_height = sum(item[2][3] - item[2][1] for item in rendered) + line_gap * (len(rendered) - 1)
    bottom_margin = max(18, int(height * 0.055))
    y = max(8, height - bottom_margin - block_height)

    pil_frame = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
    overlay = Image.new("RGBA", pil_frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fill = (*_hex_to_rgb(settings.font_color), 255)
    shadow = (0, 0, 0, 180)

    current_y = y
    for line, font, bbox in rendered:
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = max(8, (width - text_width) // 2)
        shadow_offset = max(1, int(min_side * 0.004))
        draw.text((x + shadow_offset, current_y + shadow_offset), line, font=font, fill=shadow)
        draw.text((x, current_y), line, font=font, fill=fill)
        current_y += text_height + line_gap

    pil_frame = Image.alpha_composite(pil_frame, overlay).convert("RGB")
    frame[:, :, :] = cv2.cvtColor(np.asarray(pil_frame), cv2.COLOR_RGB2BGR)


def _static_preview_spectrum(bars: int) -> np.ndarray:
    angles = np.linspace(0.0, math.tau, bars, endpoint=False)
    spectrum = (
        0.18
        + 0.42 * (np.sin(angles * 3.0 - 0.35) ** 2)
        + 0.28 * (np.sin(angles * 8.0 + 0.9) ** 2)
        + 0.12 * (np.cos(angles * 13.0) ** 2)
    )
    return np.clip(spectrum, 0.0, 1.0).astype(np.float32)


def render_static_preview(image_path: Path | str, settings: RenderSettings) -> np.ndarray:
    image = _load_image(Path(image_path))
    frame = _make_background(image, settings)
    volume = 0.58
    _draw_equalizer(frame, _static_preview_spectrum(settings.bars), volume, settings)

    pulse = 1.0 + (volume * 0.045 if settings.pulse_enabled else 0.0)
    center_size = max(2, int(settings.center_size * pulse))
    center_size += center_size % 2
    center_image = _center_image(image, center_size, settings)
    x = (settings.width - center_size) // 2
    y = (settings.height - center_size) // 2
    _overlay_rgba(frame, center_image, x, y)

    _draw_text(frame, settings)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def _mux_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    start_time: float,
    duration: float,
) -> None:
    command = [_ffmpeg(), "-y", "-i", str(video_path)]
    if start_time > 0:
        command.extend(["-ss", _format_seconds(start_time)])
    command.extend(["-t", _format_seconds(duration), "-i", str(audio_path)])
    command.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    _run_command(command, "MP4への音声合成またはH.264書き出しに失敗しました。")


def render_equalizer_video(
    *,
    image_path: Path | str,
    audio_path: Path | str,
    settings: RenderSettings,
    output_path: Path | str,
    start_time: float = 0.0,
    duration: float | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    ensure_project_dirs()
    require_ffmpeg()

    image_path = Path(image_path)
    audio_path = Path(audio_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if settings.width % 2 != 0 or settings.height % 2 != 0:
        raise RenderError("H.264 / yuv420p 出力のため、幅と高さは偶数で指定してください。")

    audio_duration = get_audio_duration(audio_path)
    start_time = max(0.0, float(start_time))
    if start_time >= audio_duration:
        raise RenderError(
            f"プレビュー開始位置が音声の長さを超えています。開始位置: {start_time:.2f}秒 / 音声: {audio_duration:.2f}秒"
        )

    if duration is None:
        render_duration = audio_duration - start_time
    else:
        render_duration = min(float(duration), audio_duration - start_time)
    if render_duration <= 0:
        raise RenderError("書き出し対象の長さが0秒以下です。")

    if progress_callback:
        progress_callback(0.02, "解析用WAVを作成しています")

    work_id = f"{timestamp_string()}_{uuid.uuid4().hex[:8]}"
    wav_path = TMP_DIR / f"analysis_{work_id}.wav"
    raw_video_path = TMP_DIR / f"video_only_{work_id}.mp4"

    try:
        _convert_audio_to_wav(audio_path, wav_path, start_time=start_time, duration=render_duration)
        samples, sample_rate = _read_wav_mono(wav_path)

        if progress_callback:
            progress_callback(0.08, "音声を解析しています")
        spectra, volumes = _analyze_spectra(samples, sample_rate, settings, render_duration)

        image = _load_image(image_path)
        background = _make_background(image, settings)
        center_cache: dict[int, np.ndarray] = {}

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(raw_video_path), fourcc, float(settings.fps), (settings.width, settings.height))
        if not writer.isOpened():
            raise RenderError("OpenCVのVideoWriterを開始できませんでした。")

        frame_count = spectra.shape[0]
        try:
            for frame_index in range(frame_count):
                frame = background.copy()
                volume = float(volumes[frame_index])
                _draw_equalizer(frame, spectra[frame_index], volume, settings)

                pulse = 1.0 + (volume * 0.045 if settings.pulse_enabled else 0.0)
                center_size = max(2, int(settings.center_size * pulse))
                center_size += center_size % 2
                if center_size not in center_cache:
                    center_cache[center_size] = _center_image(image, center_size, settings)
                center_image = center_cache[center_size]
                x = (settings.width - center_size) // 2
                y = (settings.height - center_size) // 2
                _overlay_rgba(frame, center_image, x, y)

                _draw_text(frame, settings)
                writer.write(frame)

                if progress_callback:
                    progress = 0.10 + 0.78 * ((frame_index + 1) / frame_count)
                    progress_callback(progress, f"フレームを書き出しています {frame_index + 1}/{frame_count}")
        finally:
            writer.release()

        if progress_callback:
            progress_callback(0.92, "音声を合成しています")
        _mux_audio(raw_video_path, audio_path, output_path, start_time=start_time, duration=render_duration)

        if progress_callback:
            progress_callback(1.0, "完了しました")
        return output_path
    finally:
        for path in (wav_path, raw_video_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
