from pathlib import Path

from app.services.share_export_service import ShareExportService


def test_share_filename_generation(tmp_path: Path) -> None:
    source = tmp_path / "Sylas_MANUAL_CLIP.mp4"
    source.write_bytes(b"source")
    first = ShareExportService.output_path_for(source)
    assert first.name == "Sylas_MANUAL_CLIP_share.mp4"
    first.write_bytes(b"copy")
    second = ShareExportService.output_path_for(source)
    assert second.name == "Sylas_MANUAL_CLIP_share_2.mp4"
