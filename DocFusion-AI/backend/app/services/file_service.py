from fastapi import UploadFile

from ..core.paths import UPLOAD_DIR


UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_upload_file(file: UploadFile) -> str:
    file_path = UPLOAD_DIR / file.filename

    with open(file_path, "wb") as f:
        content = file.file.read()
        f.write(content)

    return str(file_path)
